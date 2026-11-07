import os
from _pydatetime import time
from contextlib import contextmanager

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, START, END
from pathlib import Path
from langgraph.types import RetryPolicy
from agent.llm import AGENTS
from agent.state import AgentState, WORKERS
from agent.node import supervisor_node, worker_node


retry_policy=RetryPolicy(
    max_attempts=10,
    initial_interval=5.0,
    backoff_factor=3.0,
    max_interval=60,
    jitter=True,
)


def route(state: AgentState) -> str:
    if state.next_agent == "FINISH" or state.iteration >= state.max_iterations:
        return END
    return state.next_agent


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor_node)
    for name, agent in AGENTS.items():
        builder.add_node(name, worker_node(name, agent), retry_policy=retry_policy)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route, WORKERS + [END])

    for name in WORKERS:
        builder.add_edge(name, "supervisor")

    return builder


@contextmanager
def get_graph():
    with PostgresSaver.from_conn_string(os.getenv("PG_URI")) as checkpointer:
        checkpointer.setup()
        graph = build_graph().compile(checkpointer = checkpointer)
        yield graph


def run_report(pdf_filename: str, request: str = "分析这份财报，重点看现金流质量", is_retry: bool=False) -> str | None:
    with get_graph() as graph:
        stem = Path(pdf_filename).stem
        thread_id = f"report-{stem}"
        config = {"configurable": {"thread_id": thread_id},
                  "recursion_limit": 30}
        state_snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})

        if state_snapshot.next and not is_retry:
            return graph.invoke(None, config)
        else:
            if is_retry:
                config["configurable"]["thread_id"] = f"{thread_id}-retry-{time.time()}"

        result = graph.invoke(
            {
                "messages": [HumanMessage(content=request)],
                "user_request": request,
                "input_files": [pdf_filename],
            },
            config=config
        )

    report = result.get("files", {}).get("final_report.md")
    if report:
        out_dir = Path("output")
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"{stem}_report.md"
        out.write_text(report, encoding="utf-8")
        print(f"\n✅ 报告已导出: {out.resolve()}")

    return report


def main():
    request = "分析这份财报，重点看现金流质量"
    input_files = ["GLM.pdf"]
    report = run_report(input_files[0], request, is_retry=False)
    if not report:
        print("\n❌ 未生成最终报告")


if __name__ == "__main__":
    main()
