from pathlib import Path
from typing import Annotated
import pdfplumber
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from agent.state import AGENT_BY_FILE, Task, MAX_RETRY


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@tool
def parse_report(pdf_path: str) -> str:
    """解析财报PDF并提取全文文本。pdf_path 为 data 目录下的文件名，如 'changxin.pdf'。"""
    full_path = (DATA_DIR / pdf_path).resolve()

    if not full_path.is_relative_to(DATA_DIR.resolve()):
        return "错误: 路径必须位于 data 目录内"
    if not full_path.exists():
        return f"错误: 找不到文件 {full_path}"

    parts = []
    with pdfplumber.open(full_path) as pdf:
        for i, page in enumerate(pdf.pages):
            parts.append(f"--- 第{i+1}页 ---\n{page.extract_text() or ''}")
    text = "\n".join(parts)
    if not text.strip():
        return "错误: PDF 无可提取文本（可能是扫描件，需要 OCR）"
    return text[:20000]


def _bump_task(tasks: list[Task], agent_name: str, ok: bool) -> list[Task]:
    """业务规则集中地：写入即标 done / 失败计重试"""
    out = []
    for t in tasks:
        if t.assigned_to == agent_name and t.status == "pending":
            if ok:
                out.append(t.model_copy(update={"status": "done"}))
            else:
                rc = t.retry_count + 1
                status = "failed" if rc >= MAX_RETRY else "pending"
                out.append(t.model_copy(update={"status": status, "retry_count": rc}))
        else:
            out.append(t)
    return out


@tool
def write_file(path: str, content: str,
               tool_call_id: Annotated[str, InjectedToolArg()],
               runtime: Annotated[ToolRuntime, InjectedToolArg()]) -> Command:
    """将内容写入共享文件系统。path 如 'raw_report.txt'、'notes/quant.md'、'final_report.md'。"""
    state = runtime.state
    files = dict(state.get("files") or {})
    tasks = state.get("tasks") or []

    if path.startswith("/") or ".." in path:
        return Command(update={
            "messages": [ToolMessage(
                content=f"非法路径: {path}，拒绝写入",
                tool_call_id=tool_call_id)],
        })

    files[path] = content

    agent_name = AGENT_BY_FILE.get(path)
    if agent_name:
        tasks = _bump_task(tasks, agent_name, ok=True)

    return Command(update={
        # ★ 必须带：LLM 在等的"调用结果"消息
        "messages": [ToolMessage(
            content=f"已写入 {path}（{len(content)} 字符）",
            tool_call_id=tool_call_id)],
        # 业务更新：穿透到外层 AgentState
        "files": files,
        "tasks": tasks,
    })


@tool
def read_file(path: str, runtime: Annotated[ToolRuntime, InjectedToolArg()]) -> str:
    """读取共享文件系统中指定路径的文件内容。"""
    files = runtime.state.get("files") or {}
    return files.get(path, f"文件不存在: {path}。当前可用: {list(files.keys())}")
