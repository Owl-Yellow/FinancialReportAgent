import sys
import io
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    pdf_name: str = ""
    request: str = "分析这份财报，重点看现金流质量"
    is_retry: bool = False
    status: str = "pending"        # pending / running / done / failed
    logs: list[str] = field(default_factory=list)
    report: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class JobStore:
    """线程安全的任务注册表。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, job_id: str) -> Job:
        job = Job(id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


store = JobStore()


class LogCapture:
    """把 sys.stdout 的 print 捕获进 job.logs（node.py 零改动）。"""
    def __init__(self, job: Job):
        self.job = job
        self._orig = None

    def _write(self, text):
        for line in text.splitlines():
            if line.strip():
                self.job.logs.append(line)
        return len(text)

    def __enter__(self):
        self._orig = sys.stdout
        sys.stdout = io.StringIO()
        sys.stdout.write = self._write          # 劫持 write，直接转发
        return self

    def __exit__(self, *exc):
        sys.stdout = self._orig
        return False
