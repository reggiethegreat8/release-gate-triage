"""The graph as a server runs it: with the approval steps, and no storage of its own.

`langgraph dev` and a LangGraph deployment supply the checkpointer and the
store themselves, so this graph is built with neither. langgraph.json points
here. review.py builds the same graph with local SQLite files instead.
"""

from agent.graph import build_graph

graph = build_graph(approval=True)
