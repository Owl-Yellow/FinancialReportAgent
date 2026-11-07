import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from agent.llm import AGENTS            # noqa: F401（确保 worker 注册）
from agent.state import AgentState, WORKERS   # noqa: F401
from agent.node import supervisor_node, worker_node  # noqa: F401
from agent.graph import run_report

from financialreportagent.jobs import store, LogCapture

app = FastAPI(title="Financial Report Agent")
TEMPLATES = Path(__file__).parent / "templates"


class AnalyzeRequest(BaseModel):
    pdf_name: str
    request: str = "分析这份财报，重点看现金流质量"
    is_retry: bool = False


@app.get("/", response_class=HTMLResponse)
def index():
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


@app.get("/api/pdfs")
def list_pdfs():
    from agent.tools import DATA_DIR
    return {"pdfs": sorted(p.name for p in DATA_DIR.glob("*.pdf"))}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    job_id = uuid.uuid4().hex[:12]
    job = store.create(job_id)
    job.pdf_name = req.pdf_name
    job.request = req.request
    job.is_retry = req.is_retry
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


def _run_job(job_id: str):
    job = store.get(job_id)
    job.status = "running"
    try:
        with LogCapture(job):
            report = run_report(
                pdf_filename=job.pdf_name,
                request=job.request,
                is_retry=job.is_retry,
            )
        job.report = report
        job.status = "done" if report else "failed"
        if not report:
            job.error = "未生成最终报告（可能重试超限或熔断）"
    except Exception as e:
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, after: int = 0):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "pdf_name": job.pdf_name,
        "is_retry": job.is_retry,
        "log_lines": job.logs[after:],     # 增量日志
        "log_total": len(job.logs),
        "report": job.report,
    }


@app.get("/api/jobs/{job_id}/report", response_class=PlainTextResponse)
def download_report(job_id: str):
    job = store.get(job_id)
    if not job or not job.report:
        raise HTTPException(404, "report not ready")
    return job.report


def run():
    uvicorn.run(
        "financialreportagent.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    run()
