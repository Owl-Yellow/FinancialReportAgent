from agent.state import AgentState, Task, FILE_BY_AGENT, PIPELINE, MAX_RETRY


def supervisor_node(state: AgentState) -> dict:
    """代码路由：按流水线顺序找第一个产物缺失的环节派发；产物齐全则 FINISH。
    监督完全基于 files（唯一真相来源），不依赖 LLM。"""
    files = dict(state.files)
    tasks = list(state.tasks)

    # ── 首次进入：创建 ingestion 任务（幂等）──
    if not any(t.assigned_to == "ingestion" for t in tasks):
        tasks.append(Task(
            id="task_ingest_001",
            description=f"解析 {', '.join(state.input_files)} 并生成 raw_report.txt",
            assigned_to="ingestion",
        ))

    # ── 产物出现即 done（顺手校正任务状态）──
    for i, t in enumerate(tasks):
        expected = FILE_BY_AGENT.get(t.assigned_to)
        if expected and expected in files and t.status == "pending":
            tasks[i] = t.model_copy(update={"status": "done"})
            print(f"✅ 任务 {t.id} 已确认完成（产物 {expected}）")

    # ── 路由：流水线顺序找第一个缺失环节 ──
    next_agent, reason = "FINISH", "全部产物已生成"
    for stage in PIPELINE:
        if FILE_BY_AGENT[stage] not in files:
            next_agent, reason = stage, f"产物 {FILE_BY_AGENT[stage]} 缺失"
            break

    # ── 打回循环：review 已存在且结论为打回 ──
    files_changed = False
    if next_agent == "writer" and "notes/review.md" in files \
            and "打回" in files["notes/review.md"][:200]:
        print("⚠️ review 结论为打回，清空 quant/qualitative/review 产物，重做")
        files = {k: v for k, v in files.items()
                 if k not in ("notes/quant.md", "notes/qualitative.md", "notes/review.md")}
        files_changed = True
        for i, t in enumerate(tasks):
            if t.assigned_to in ("quant", "qualitative") and t.status != "pending":
                if t.retry_count < MAX_RETRY:
                    tasks[i] = t.model_copy(update={"status": "pending"})
        next_agent, reason = "quant", "review 打回，重做分析"

    # ── 失败计数与终止：本次要派的环节，其 pending 任务计一次派发 ──
    if next_agent != "FINISH":
        for i, t in enumerate(tasks):
            if t.assigned_to == next_agent and t.status == "pending":
                rc = t.retry_count + 1
                if rc > MAX_RETRY:
                    tasks[i] = t.model_copy(update={"status": "failed"})
                    print(f"❌ 任务 {t.id} 重试超限，标记 failed，终止流程")
                    next_agent, reason = "FINISH", "任务失败超限"
                else:
                    tasks[i] = t.model_copy(update={"retry_count": rc})

    # ── 迭代上限兜底 ──
    if state.iteration + 1 >= state.max_iterations:
        print(f"⚠️ 达到迭代上限 {state.max_iterations}，终止")
        next_agent, reason = "FINISH", "迭代超限"

    print(f"[Supervisor #{state.iteration}] → {next_agent} | {reason}")
    print(f"    [state] files={list(files.keys())} "
          f"tasks={[(t.id, t.assigned_to, t.status) for t in tasks]}")

    ret = {"next_agent": next_agent, "tasks": tasks,
           "iteration": state.iteration + 1}
    if files_changed:
        ret["files"] = files
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
        print(f">>> This is agent {agent_name}\n")
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
        expected = FILE_BY_AGENT.get(agent_name)
        tasks = []
        for t in state.tasks:
            if (t.assigned_to == agent_name and t.status == "pending"
                    and expected and expected in new_files):
                tasks.append(t.model_copy(update={"status": "done"}))
                print(f"✅ 任务 {t.id} 完成（产物 {expected}）")
            elif t.assigned_to == agent_name and t.status == "pending" \
                    and expected and expected not in new_files:
                print(f"⚠️ 任务 {t.id} 未交付产物 {expected}，保持 pending 等待重派")
                tasks.append(t)
            else:
                tasks.append(t)

        return {
            "files": new_files,
            "tasks": tasks,
            "messages": result["messages"][-1:],
        }
    return node
