from typing import Annotated, Literal, get_args
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


AgentName = Literal["ingestion", "quant", "qualitative", "reviewer", "writer", "FINISH"]
TaskStatus = Literal["pending", "done", "failed"]
MAX_RETRY = 3
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
    user_request: str
    input_files: list[str] = []
    files: dict[str, str] = {}        # 共享虚拟文件系统（内存）
    tasks: list[Task] = []
    next_agent: str = ""
    iteration: int = 0
    max_iterations: int = 10


FILE_BY_AGENT = {
    "ingestion": "raw_report.txt",
    "quant": "notes/quant.md",
    "qualitative": "notes/qualitative.md",
    "reviewer": "notes/review.md",
    "writer": "final_report.md",
}
AGENT_BY_FILE = {v: k for k, v in FILE_BY_AGENT.items()}
