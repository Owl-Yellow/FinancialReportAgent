import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from agent.graph import run_report
from agent.tools import DATA_DIR

app = FastAPI(title="财报分析 Agent")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
ALLOWED_SUFFIX = {".pdf"}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "result": None})


@app.post("/analyze")
async def analyze(
    request: Request,
    file: UploadFile = File(...),
    focus: str = Form("分析这份财报，重点看现金流质量"),
):
    # ---- 校验后缀 ----
    if Path(file.filename or "").suffix.lower() not in ALLOWED_SUFFIX:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "result": None,
            "error": "仅支持 PDF 文件",
        })

    # ---- 保存上传文件到 data 目录（重命名避免冲突）----
    stem = Path(file.filename).stem or "report"
    safe_name = f"{stem}_{uuid.uuid4().hex[:8]}.pdf"
    saved = DATA_DIR / safe_name
    with saved.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # ---- 跑流水线（同步，可能要几分钟）----
    try:
        report_text = run_report(safe_name, focus)
    finally:
        saved.unlink(missing_ok=True)   # 用完删掉上传的临时文件

    if not report_text:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "result": None,
            "error": "流水线未产出最终报告，请检查后端日志",
        })

    # ---- 导出为下载文件 ----
    out = OUTPUT_DIR / f"{stem}_result.md"
    out.write_text(report_text, encoding="utf-8")

    return templates.TemplateResponse("index.html", {
        "request": request,
        "result": f"/download/{out.name}",
        "filename": out.name,
        "preview": report_text[:2000],
    })


@app.get("/download/{name}")
async def download(name: str):
    path = OUTPUT_DIR / name
    if not path.exists() or not path.is_relative_to(OUTPUT_DIR.resolve()):
        return HTMLResponse("文件不存在", status_code=404)
    return FileResponse(path, filename=name, media_type="text/markdown")
