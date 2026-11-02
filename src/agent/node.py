from agent.llm import supervisor_llm
from agent.state import AgentState, FILE_BY_AGENT, MAX_RETRY
from agent.system_prompt import SUPERVISOR_PROMPT


def supervisor_node(state: AgentState) -> dict:
    decision = supervisor_llm.invoke(SUPERVISOR_PROMPT.format(
        input_files=", ".join(state.input_files) or "(无)",
        files=", ".join(state.files.keys()) or "(空)",
        tasks="\n".join(f"{t.id}|{t.assigned_to}|{t.status}|{t.description}" for t in state.tasks) or "(无)",
        user_request=state.user_request,
    ))

    task_updates = getattr(decision, "task_updates", []) or []
    new_tasks = getattr(decision, "new_tasks", []) or []

    task_ids = {t.id: t for t in state.tasks}
    for t in task_updates:
        if t.id in task_ids:
            expected = FILE_BY_AGENT.get(t.assigned_to)
            if t.status == "pending" and expected and expected not in state.files:
                rc = task_ids[t.id].retry_count + 1
                t = t.model_copy(update={
                    "retry_count": rc,
                    "status": "failed" if rc >= MAX_RETRY else "pending",
                })
                print(f"⚠️ 任务 {t.id} 产物 {expected} 缺失，重试 {rc}/{MAX_RETRY}")
            else:
                t.retry_count = task_ids[t.id].retry_count + 1
                if t.status == "pending" and t.retry_count >= MAX_RETRY:
                    t.status = "failed"
                    print(f"⚠️ 任务 {t.id} 重试超限，标记 failed")
        task_ids[t.id] = t
    for t in new_tasks:
        if t.id not in task_ids:
            task_ids[t.id] = t

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
                    for t in task_ids.values():
                        if t.assigned_to in ("quant", "qualitative") \
                                and t.status != "pending" and t.retry_count < MAX_RETRY:
                            t.status = "pending"
                    decision.next_agent = "quant"
                    decision.reason = "review 打回，重做 quant"
                    print(f"    [state] 清除产物后 files={list(files_override.keys())}")
                else:
                    decision.next_agent = "writer"
                    decision.reason = "review 已通过，派 writer"
                    print("⚠️ review 已通过，强制派 writer")
            else:
                print(f"⚠️ 拦截重派 {decision.next_agent}（产物 {f} 已存在）")
                if "final_report.md" in state.files:
                    decision.next_agent = "FINISH"
                else:
                    decision.next_agent = "writer"
                decision.reason = f"拦截重派，改派 {decision.next_agent}"

    if "final_report.md" in state.files:
        decision.next_agent = "FINISH"
        decision.reason = "final_report.md 已生成，强制结束"

    print(f"[Supervisor #{state.iteration}] → {decision.next_agent} | {decision.reason}")
    print(f"    [state] files={list(state.files.keys())} "
          f"tasks={[(t.id, t.assigned_to, t.status) for t in task_ids.values()]}")

    ret = {
        "next_agent": decision.next_agent,
        "tasks": list(task_ids.values()),
        "iteration": state.iteration + 1,
    }
    if files_override is not None:
        ret["files"] = files_override
    return ret


def worker_node(agent_name: str, agent):
    """拼任务消息（含工作区全文）→ 跑 agent → 从 tool_calls 提取 write_file 落盘 → 更新任务状态。"""
    def node(state: AgentState):
        my_tasks = [t for t in state.tasks
                    if t.assigned_to == agent_name and t.status == "pending"]
        task_desc = "\n".join(f"[{t.id}] {t.description}" for t in my_tasks) or "(无待办任务)"

        workspace = dict(state.files or {})
        existing = "\n\n".join(
            f"=== 文件: {k} ===\n{v[:12000]}"
            for k, v in workspace.items()
        ) or "(空)"

        result = agent.invoke({"messages": [("user",
            f"用户需求: {state.user_request}\n"
            f"输入文件: {', '.join(state.input_files)}\n"
            f"当前工作区（可直接引用，无需任何读取工具）:\n{existing}\n"
            f"分派给你的任务:\n{task_desc}")]})

        print(f"[{agent_name}] 完成")

        # 落盘：扫所有消息里的 write_file 工具调用
        new_files = dict(workspace)
        for m in result["messages"]:
            for call in (getattr(m, "tool_calls", None) or []):
                if call["name"] == "write_file":
                    args = call["args"]
                    path, content = args["path"], args["content"]
                    if not path.startswith("/") and ".." not in path:
                        new_files[path] = content
                        print(f"📝 落盘 {path}（{len(content)} 字符）")

        # 产物出现即 done
        expected = FILE_BY_AGENT.get(agent_name)
        tasks = []
        for t in state.tasks:
            if (t.assigned_to == agent_name and t.status == "pending"
                    and expected and expected in new_files):
                tasks.append(t.model_copy(update={"status": "done"}))
                print(f"✅ 任务 {t.id} 完成（产物 {expected}）")
            else:
                tasks.append(t)

        return {
            "files": new_files,
            "tasks": tasks,
            "messages": result["messages"][-1:],
        }
    return node
