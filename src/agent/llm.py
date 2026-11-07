import os
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph_sdk.errors import APIStatusError
from agent.state import RouteDecision
from agent.tools import parse_report, write_file
from agent.system_prompt import (INGESTION_PROMPT, QUANT_PROMPT, QUALITATIVE_PROMPT,
                                 REVIEWER_PROMPT, WRITER_PROMPT)

load_dotenv()

llm_model = ChatOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL"),
    model="deepseek-flash",
    max_retries=5,
    timeout=120,
    # reasoning_effort="high",
    # extra_body={"thinking": {"type": "enabled"}},
    temperature=0.7,
)

supervisor_llm = llm_model.with_structured_output(RouteDecision).with_retry(
    stop_after_attempt=6,           # 最多试 6 次（共等待约 1 分钟）
    wait_exponential_jitter=True,   # 等待时间指数增长 + 随机抖动
    retry_if_exception_type=APIStatusError,  # 只重试 API 类错误（503等）
)

assert "task_updates" in RouteDecision.model_fields, \
    "RouteDecision 缺少 task_updates 字段，检查 import 来源是否唯一"

COMMON_TOOLS = [parse_report, write_file]

ingestion_agent = create_agent(llm_model, tools=COMMON_TOOLS, system_prompt=INGESTION_PROMPT)
quant_agent = create_agent(llm_model, tools=COMMON_TOOLS, system_prompt=QUANT_PROMPT)
qualitative_agent = create_agent(llm_model, tools=COMMON_TOOLS, system_prompt=QUALITATIVE_PROMPT)
reviewer_agent = create_agent(llm_model, tools=COMMON_TOOLS, system_prompt=REVIEWER_PROMPT)
writer_agent = create_agent(llm_model, tools=COMMON_TOOLS, system_prompt=WRITER_PROMPT)

AGENTS: dict = {
    "ingestion": ingestion_agent,
    "quant": quant_agent,
    "qualitative": qualitative_agent,
    "reviewer": reviewer_agent,
    "writer": writer_agent,
}
