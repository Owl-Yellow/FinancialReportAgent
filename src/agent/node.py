from agent.state import AgentState, Task, FILE_BY_AGENT, PIPELINE, MAX_RETRY_ROUNDS, PIPELINE_FILES, derive_tasks, \
    REJECTED_FILES, REJECTED_STAGES


def supervisor_node(state) -> dict:
    """files 为唯一真相。决策管线：路由 → 打回修正 → 终止检查。"""
    files = dict(state.files)
    retry_counts = dict(state.retry_counts)
    review_rounds = state.review_rounds
    iteration = state.iteration
    max_iterations = state.max_iterations
    files_changed = False

    # ── 1. 路由：流水线顺序找第一个缺失产物 ──
    next_agent = next(
        (stage for stage, file in PIPELINE_FILES if file not in files),
        "FINISH",
    )

    # ── 2. 打回修正：review 不通过 → 清分析产物、路由回 quant ──
    if _review_rejects(files):
        if review_rounds < MAX_RETRY_ROUNDS:
            review_rounds += 1
            files = {k: v for k, v in files.items() if k not in REJECTED_FILES}
            retry_counts = {k: v for k, v in retry_counts.items()
                            if k not in REJECTED_STAGES}
            next_agent = "quant"
            files_changed = True
            print(f"⚠️ review 打回（第 {review_rounds}/{MAX_RETRY_ROUNDS} 轮），"
                  f"清空分析产物，重做")
        else:
            print(f"⚠️ 打回预算耗尽（{review_rounds} 轮），强制通过")
            next_agent = "writer"

    # ── 3a. 熔断：本次派发的环节计一次，超限终止 ──
    if next_agent != "FINISH":
        retry_counts[next_agent] = retry_counts.get(next_agent, 0) + 1
        if retry_counts[next_agent] > MAX_RETRY_ROUNDS:
            print(f"❌ {next_agent} 环节派发 {retry_counts[next_agent] - 1} 次"
                  f"仍无产物，熔断终止")
            next_agent = "FINISH"

    # ── 3b. 迭代上限兜底 ──
    if iteration + 1 >= max_iterations:
        print(f"⚠️ 达到迭代上限 {max_iterations}，终止")
        next_agent = "FINISH"

    # ── 4. 输出 ──
    print(f"[Supervisor #{iteration}] → {next_agent}")
    print(f"    files={sorted(files)} retries={retry_counts} "
          f"review_rounds={review_rounds}")

    ret = {
        "next_agent": next_agent,
        "iteration": iteration + 1,
        "review_rounds": review_rounds,
        "retry_counts": retry_counts,
    }
    if files_changed:
        ret["files"] = files
    return ret


def _review_rejects(files: dict[str, str]) -> bool:
    """review 已产出且结论为打回（前 200 字内出现关键词）。"""
    review = files.get("notes/review.md")
    return bool(review and "打回" in review[:200])


def worker_node(agent_name: str, agent):
    """拼任务消息（含工作区全文）→ 跑 agent → 从 tool_calls 提取 write_file 落盘。"""
    expected_file = FILE_BY_AGENT.get(agent_name)

    def node(state):
        task_desc = (f"完成产物 {expected_file}（{agent_name} 环节）"
                     if expected_file else "(无指定产物)")

        workspace = dict(state.files)
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

        if expected_file and expected_file not in new_files:
            print(f"⚠️ {agent_name} 未交付 {expected_file}，等待重派")

        return {
            "files": new_files,
            "messages": result["messages"][-1:],
        }
    return node


