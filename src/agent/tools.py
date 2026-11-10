from pathlib import Path
import pdfplumber
from langchain_core.tools import tool
from agent.rag import STORE


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@tool
def parse_report(pdf_path: str) -> str:
    """解析财报PDF并提取全文文本。pdf_path 为 data 目录下的文件名，如 'BankOfChina.pdf'。"""
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

    STORE.build(Path(pdf_path).stem, text)

    return text[:20000]


@tool
def write_file(path: str, content: str) -> str:
    """将产物写入共享文件系统。path 如 'raw_report.txt'、'notes/quant.md'、'final_report.md'。
    调用成功后内容即生效，返回写入确认。"""
    if path.startswith("/") or ".." in path:
        return f"错误: 非法路径 {path}，拒绝写入"
    # 纯回执：真正写入文件由 worker_node 从 tool_calls 记录中提取
    return f"已写入 {path}（{len(content)} 字符）"
