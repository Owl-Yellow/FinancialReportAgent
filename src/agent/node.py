from agent.state import AgentState, Task, FILE_BY_AGENT, PIPELINE, MAX_RETRY_ROUNDS


def supervisor_node(state: AgentState) -> dict:
    """代码路由：按流水线顺序找第一个产物缺失的环节派发。
    控制依据 = files（唯一真相）；tasks = 完整的进度账本（审计/可观测）。"""
    files = dict(state.files)
    tasks = list(state.tasks)

    # ── 幂等建任务：每个产物尚缺的环节，账上必须有一条任务 ──
    for stage in PIPELINE:
        f = FILE_BY_AGENT[stage]
        if f not in files and not any(t.assigned_to == stage for t in tasks):
            tasks.append(Task(
                id=f"task_{stage}_001",
                description=f"完成产物 {f}（{stage} 环节）",
                assigned_to=stage,
            ))
            print(f"📋 建任务 task_{stage}_001 → {stage}")

    # ── 对账：产物出现 → pending 变 done（与真相同步）──
    for i, t in enumerate(tasks):
        expected_files = FILE_BY_AGENT.get(t.assigned_to)
        if expected_files and expected_files in files and t.status == "pending":
            tasks[i] = t.model_copy(update={"status": "done"})
            print(f"✅ 任务 {t.id} 已确认完成（产物 {expected_files}）")
        elif expected_files and t.status == "done" and expected_files not in files:
            tasks[i] = t.model_copy(update={"status": "pending"})
            print(f"↩️ 任务 {t.id} 产物已不在工作区，回退 pending")

    # ── 路由：流水线顺序找第一个缺失环节（控制流，不看 tasks）──
    next_agent, reason = "FINISH", "全部产物已生成"
    for stage in PIPELINE:
        if FILE_BY_AGENT[stage] not in files:
            next_agent, reason = stage, f"产物 {FILE_BY_AGENT[stage]} 缺失"
            break

    # ── 打回循环 ──
    is_files_changed = False
    review_rounds = state.review_rounds

    if next_agent == "writer" and "notes/review.md" in files and "打回" in files["notes/review.md"][:200]:
        if review_rounds >= MAX_RETRY_ROUNDS:
            print("⚠️ review 结论为打回，清空 quant/qualitative/review 产物，重做")
            next_agent, reason = "writer", f"打回预算耗尽（{review_rounds} 轮），强制通过"
        else:
            review_rounds += 1
            print(f"⚠️ review 结论为打回（第 {review_rounds}/{MAX_RETRY_ROUNDS} 轮），"
                  f"清空 quant/qualitative/review 产物，重做")
            files = {k: v for k, v in files.items() if k not in ("notes/quant.md", "notes/qualitative.md", "notes/review.md")}
            is_files_changed = True
            for i, t in enumerate(tasks):
                if t.assigned_to in ("quant", "qualitative", "reviewer") and t.status != "pending":
                    tasks[i] = t.model_copy(update={"status": "pending"})
            next_agent, reason = "quant", "review 打回，重做分析"

    # ── 失败计数与终止：本次要派的环节，其 pending 任务计一次派发 ──
    if next_agent != "FINISH":
        for i, t in enumerate(tasks):
            if t.assigned_to == next_agent and t.status == "pending":
                retry_count = t.retry_count + 1
                if retry_count > MAX_RETRY_ROUNDS:
                    tasks[i] = t.model_copy(update={"status": "failed"})
                    print(f"❌ 任务 {t.id} 重试 {retry_count-1} 次仍失败，标记 failed，终止流程")
                    next_agent, reason = "FINISH", "任务失败超限"
                else:
                    tasks[i] = t.model_copy(update={"retry_count": retry_count})

    # ── 迭代上限兜底 ──
    if state.iteration + 1 >= state.max_iterations:
        print(f"⚠️ 达到迭代上限 {state.max_iterations}，终止")
        next_agent, reason = "FINISH", "迭代超限"

    print(f"[Supervisor #{state.iteration}] → {next_agent} | {reason}")
    print(f"    [state] files={list(files.keys())} "
          f"tasks={[(t.id, t.assigned_to, t.status) for t in tasks]}")

    ret = {"next_agent": next_agent, "tasks": tasks,
           "iteration": state.iteration + 1,
           "review_rounds": review_rounds,}
    if is_files_changed:
        ret["files"] = files
    return ret


def worker_node(agent_name: str, agent):
    """拼任务消息（含工作区全文）→ 跑 agent → 从 tool_calls 提取 write_file 落盘 → 更新任务状态。"""
    def node(state: AgentState):
        my_tasks = [t for t in state.tasks if t.assigned_to == agent_name and t.status == "pending"]
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
                    else:
                        print(f"⚠️ 已拦截非法路径写入: {path}")

        # 产物出现即 done；缺失则保持 pending
        expected_files = FILE_BY_AGENT.get(agent_name)
        tasks = []
        for t in state.tasks:
            if (t.assigned_to == agent_name and t.status == "pending"
                    and expected_files and expected_files in new_files):
                tasks.append(t.model_copy(update={"status": "done"}))
                print(f"✅ 任务 {t.id} 完成（产物 {expected_files}）")
            elif (t.assigned_to == agent_name and t.status == "pending"
                    and expected_files and expected_files not in new_files):
                print(f"⚠️ 任务 {t.id} 未交付产物 {expected_files}，保持 pending 等待重派")
                tasks.append(t)
            else:
                tasks.append(t)

        return {
            "files": new_files,
            "tasks": tasks,
            "messages": result["messages"][-1:],
        }
    return node



def worker_node(agent_name: str, agent):
    """拼任务消息（含工作区全文）→ 跑 agent → 从 tool_calls 提取 write_file 落盘 → 更新任务状态。"""
    def node(state: AgentState):
        # find the first tasks that is still un done.
        my_tasks = [t for t in state.tasks if t.assigned_to == agent_name and t.status == "pending"]
        task_desc = "\n".join(f"[{t.id}] {t.description}" for t in my_tasks) or "(无待办任务)"

        workspace = dict(state.files or {})
        existing = "\n\n".join(
            f"=== 文件: {k} ===\n{v[:12000]}"
            for k, v in workspace.items()
        ) or "(空)"
        print(f"\n>>> This is agent {agent_name}")
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
                    else:
                        print(f"⚠️ 已拦截非法路径写入: {path}")

        # 产物出现即 done；缺失则保持 pending（supervisor 将重派）
        expected_files = FILE_BY_AGENT.get(agent_name)
        tasks = []
        for t in state.tasks:
            if (t.assigned_to == agent_name and t.status == "pending"
                    and expected_files and expected_files in new_files):
                tasks.append(t.model_copy(update={"status": "done"}))
                print(f"✅ 任务 {t.id} 完成（产物 {expected_files}）")
            elif t.assigned_to == agent_name and t.status == "pending" \
                    and expected_files and expected_files not in new_files:
                print(f"⚠️ 任务 {t.id} 未交付产物 {expected_files}，保持 pending 等待重派")
                tasks.append(t)
            else:
                tasks.append(t)

        return {
            "files": new_files,
            "tasks": tasks,
            "messages": result["messages"][-1:],
        }
    return node
