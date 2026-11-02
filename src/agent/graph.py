from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from pathlib import Path

from agent.llm import AGENTS
from agent.state import AgentState, WORKERS
from agent.node import supervisor_node, worker_node

# ── 建图 ──
builder = StateGraph(AgentState)
builder.add_node("supervisor", supervisor_node)

for name, agent in AGENTS.items():
    builder.add_node(name, worker_node(name, agent))

def route(state: AgentState) -> str:
    if state.next_agent == "FINISH" or state.iteration >= state.max_iterations:
        return END
    return state.next_agent

builder.add_edge(START, "supervisor")
builder.add_conditional_edges("supervisor", route, WORKERS + [END])
for name in WORKERS:
    builder.add_edge(name, "supervisor")

graph = builder.compile()


def run_report(pdf_filename: str,
               request: str = "分析这份财报，重点看现金流质量") -> str | None:
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=request)],
            "user_request": request,
            "input_files": [pdf_filename],
        },
        config={"recursion_limit": 30},   # ★ 每轮 supervisor+worker 占 2 步，10 轮需 20+，留余量
    )
    return result.get("files", {}).get("final_report.md")


def main():
    request = "分析这份财报，重点看现金流质量"
    input_files = ["BankOfChina.pdf"]
    report = run_report(input_files[0], request)

    if report:
        stem = Path(input_files[0]).stem
        out_dir = Path("output")
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"{stem}_report.md"
        out.write_text(report, encoding="utf-8")
        print(f"\n✅ 报告已导出: {out.resolve()}")
    else:
        print("\n❌ 未生成最终报告")


if __name__ == "__main__":
    main()
