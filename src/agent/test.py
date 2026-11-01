from agent.state import AgentState, AgentName
from langgraph.graph import StateGraph, START, END

a = AgentName
b = a.add(END)
print(b)

