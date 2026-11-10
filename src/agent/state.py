from typing import Annotated, Literal, get_args
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


AgentName = Literal["ingestion", "quant", "qualitative", "reviewer", "writer", "FINISH"]
TaskStatus = Literal["pending", "done", "failed"]
MAX_RETRY_ROUNDS = 2
WORKERS = [a for a in get_args(AgentName) if a != "FINISH"]
PIPELINE = ["ingestion", "quant", "qualitative", "reviewer", "writer"]


class Task(BaseModel):
    id: str
    description: str
    assigned_to: AgentName
    status: TaskStatus = "pending"
    retry_count: int = 0


class RouteDecision(BaseModel):
    next_agent: AgentName
    reason: str
    new_tasks: list[Task] = Field(default_factory=list)
    task_updates: list[Task] = Field(
        default_factory=list,
        description="需要更新状态的任务（携带新 status），如 reviewer 打回重做")


class AgentState(BaseModel):
    messages: Annotated[list, add_messages]
    user_request: str = ""                    # 默认值：首轮 invoke 已传，续跑时防缺键
    input_files: list[str] = []
    files: dict[str, str] = {}                # 共享虚拟文件系统（内存）—— 唯一真相
    retry_counts: dict[str, int] = {}
    next_agent: str = ""
    iteration: int = 0
    max_iterations: int = 20
    review_rounds: int = 0                    # 打回轮数（全局预算）


FILE_BY_AGENT = {
    "ingestion": "raw_report.txt",
    "quant": "notes/quant.md",
    "qualitative": "notes/qualitative.md",
    "reviewer": "notes/review.md",
    "writer": "final_report.md",
}

REJECTED_STAGES = ("quant", "qualitative", "reviewer")

REJECTED_FILES = {"notes/quant.md", "notes/qualitative.md", "notes/review.md"}

PIPELINE_FILES = [(s, FILE_BY_AGENT[s]) for s in PIPELINE]

AGENT_BY_FILE = {v: k for k, v in FILE_BY_AGENT.items()}


def derive_tasks(files: dict[str, str], retry_counts: dict[str, int]) -> list[dict]:
    """派生视图：展示/审计用，不在 state 里维护。"""
    return [
        {"stage": s, "file": f, "done": f in files, "retries": retry_counts.get(s, 0)}
        for s, f in PIPELINE_FILES
    ]
