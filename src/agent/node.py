from agent.llm import supervisor_llm
from agent.state import AgentState, FILE_BY_AGENT, MAX_RETRY
from agent.system_prompt import SUPERVISOR_PROMPT


def supervisor_node(state: AgentState) -> dict:
    decision = supervisor_llm.invoke(SUPERVISOR_PROMPT.format(
        input_files=", ".join(state.input_files) or "(无)",
        files=", ".join(state.files.keys()) or "(空)",
        tasks="\n".join(f"{t.id}|{t.assigned_to}|{t.status}|{t.description}"
                        for t in state.tasks) or "(无)",
        user_request=state.user_request,
    ))

    # ---- 容错取值 ----
    task_updates = getattr(decision, "task_updates", []) or []
    new_tasks = getattr(decision, "new_tasks", []) or []

    # ---- 任务合并：更新 + 去重新增 ----
    tasks = {t.id: t for t in state.tasks}
    for t in task_updates:
        if t.id in tasks:
            # ★ 新增：LLM 重派但产物确实没出来 → 真实计一次重试（替代原闭包的产物校验）
            expected = FILE_BY_AGENT.get(t.assigned_to)
            if t.status == "pending" and expected and expected not in state.files:
                rc = tasks[t.id].retry_count + 1
                t = t.model_copy(update={
                    "retry_count": rc,
                    "status": "failed" if rc >= MAX_RETRY else "pending",
                })
                print(f"⚠️ 任务 {t.id} 产物 {expected} 缺失，重试 {rc}/{MAX_RETRY}")
            else:
                t.retry_count = tasks[t.id].retry_count + 1
                if t.status == "pending" and t.retry_count >= MAX_RETRY:
                    t.status = "failed"
                    print(f"⚠️ 任务 {t.id} 重试超限，标记 failed")
        tasks[t.id] = t
    for t in new_tasks:
        if t.id not in tasks:
            tasks[t.id] = t

    # ---- 防重复派发 + review 结论路由 ----（原样保留）
    files_override = None

    if decision.next_agent in FILE_BY_AGENT:
        f = FILE_BY_AGENT[decision.next_agent]

        if f in state.files:
            if decision.next_agent == "reviewer":
                review = state.files.get(f, "")
                if "打回" in review[:200]:
                    print("⚠️ review 结论为打回，重置 quant/qualitative 产物")
                    files_override = {k: v for k, v in state.files.items()
                                      if not k.startswith("notes/quant")
                                      and not k.startswith("notes/qualitative")
                                      and k != "notes/review.md"}
                    for t in tasks.values():
                        if t.assigned_to in ("quant", "qualitative") \
                                and t.status != "pending" and t.retry_count < MAX_RETRY:
                            t.status = "pending"
                    decision.next_agent = "quant"
                    decision.reason = "review 打回，重做 quant"
                    print(f"    [state] 清除产物后 files={list(files_override.keys())}")
                else:
                    decision.next_agent = "writer"
                    decision.reason = "review 已通过（review.md 存在且未打回），派 writer"
                    print("⚠️ review 已通过，强制派 writer")
            else:
                print(f"⚠️ 拦截重派 {decision.next_agent}（产物 {f} 已存在）")
                if "final_report.md" in state.files:
                    decision.next_agent = "FINISH"
                else:
                    decision.next_agent = "writer"
                decision.reason = f"拦截重派，改派 {decision.next_agent}"

    # ---- 强制 FINISH 兜底 ----
    if "final_report.md" in state.files:
        decision.next_agent = "FINISH"
        decision.reason = "final_report.md 已生成，强制结束"

    print(f"[Supervisor #{state.iteration}] → {decision.next_agent} | {decision.reason}")
    print(f"    [state] files={list(state.files.keys())} "
          f"tasks={[(t.id, t.assigned_to, t.status) for t in tasks.values()]}")

    ret = {
        "next_agent": decision.next_agent,
        "tasks": list(tasks.values()),
        "iteration": state.iteration + 1,
    }
    if files_override is not None:
        ret["files"] = files_override
    return ret


def worker_node(agent_name: str, agent):
    """轻量包装：只负责把任务上下文拼成消息喂给 agent。
    产物校验已前移到 write_file（写入即标 done）和 supervisor（产物缺失计重试）。"""
    def node(state: AgentState):
        my_tasks = [t for t in state.tasks
                    if t.assigned_to == agent_name and t.status == "pending"]
        task_desc = "\n".join(f"[{t.id}] {t.description}" for t in my_tasks) or "(无待办任务)"

        files_info = f"输入文件: {', '.join(state.input_files)}" if state.input_files else ""
        existing = ", ".join(state.files.keys()) or "(空)"

        result = agent.invoke({"messages": [("user",
            f"用户需求: {state.user_request}\n"
            f"{files_info}\n"
            f"当前已有文件: {existing}\n"
            f"分派给你的任务:\n{task_desc}")]})

        print(f"[{agent_name}] 完成")
        # files / tasks 由 write_file 的 Command 更新，此处只回传 messages
        return {"messages": result["messages"][-1:]}   # 只回传增量，避免外层 messages 无限膨胀
    return node
