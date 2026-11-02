from importlib.metadata import version
import langchain_core
print("langchain-core:", langchain_core.__version__)
print("langgraph:", version("langgraph"))
print("langchain:", version("langchain"))

from agent.tools import write_file
schema = write_file.args_schema.model_json_schema()
print("required:", schema.get("required"))
print("properties:", list(schema.get("properties", {}).keys()))
