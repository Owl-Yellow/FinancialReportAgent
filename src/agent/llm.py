import os
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_qwq import ChatQwen
from agent.state import RouteDecision
from agent.tools import parse_report, write_file
from agent.system_prompt import (INGESTION_PROMPT, QUANT_PROMPT, QUALITATIVE_PROMPT,
                                 REVIEWER_PROMPT, WRITER_PROMPT)

load_dotenv()

qwen_model = ChatQwen(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
    model="deepseek-v4-pro",
)

supervisor_llm = qwen_model.with_structured_output(RouteDecision)

assert "task_updates" in RouteDecision.model_fields, \
    "RouteDecision 缺少 task_updates 字段，检查 import 来源是否唯一"

COMMON_TOOLS = [parse_report, write_file]

ingestion_agent = create_agent(qwen_model, tools=COMMON_TOOLS, system_prompt=INGESTION_PROMPT)
quant_agent = create_agent(qwen_model, tools=COMMON_TOOLS, system_prompt=QUANT_PROMPT)
qualitative_agent = create_agent(qwen_model, tools=COMMON_TOOLS, system_prompt=QUALITATIVE_PROMPT)
reviewer_agent = create_agent(qwen_model, tools=COMMON_TOOLS, system_prompt=REVIEWER_PROMPT)
writer_agent = create_agent(qwen_model, tools=COMMON_TOOLS, system_prompt=WRITER_PROMPT)

AGENTS: dict = {
    "ingestion": ingestion_agent,
    "quant": quant_agent,
    "qualitative": qualitative_agent,
    "reviewer": reviewer_agent,
    "writer": writer_agent,
}
