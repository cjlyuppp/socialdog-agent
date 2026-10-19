"""Responses-format LLM adapters and local SocialDog research tools."""

from .agent import ResearchAgent
from .llm import DeepSeekLLM, LLMResponse, ResponsesLLM
from .tools import Tool, ToolRegistry, build_research_tools
from .history_tools import build_history_tools

__all__ = ["ResearchAgent", "DeepSeekLLM", "LLMResponse", "ResponsesLLM",
           "Tool", "ToolRegistry", "build_research_tools", "build_history_tools"]
