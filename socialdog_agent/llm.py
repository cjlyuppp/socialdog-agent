"""Provider-independent Responses API contract; DeepSeek is the first adapter."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import math
import socket
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class LLMError(RuntimeError):
    """Safe, user-readable error. Never includes credentials or response bodies."""


@dataclass
class LLMResponse:
    id: str
    model: str
    output: list[dict[str, Any]]
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def output_text(self) -> str:
        return "\n".join(part["text"] for item in self.output
                         if item.get("type") == "message"
                         for part in item.get("content", [])
                         if part.get("type") == "output_text")

    @property
    def function_calls(self) -> list[dict[str, Any]]:
        return [item for item in self.output if item.get("type") == "function_call"]


class ResponsesLLM(ABC):
    """The agent only depends on this interface, never on a provider SDK."""

    @abstractmethod
    def create_response(self, *, input: list[dict], instructions: str,
                        tools: list[dict], tool_choice: str = "auto") -> LLMResponse:
        """Return Responses output items, including function_call items."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward a Bearer credential to a redirected endpoint.
        return None


REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")


class ResponsesAPILLM(ResponsesLLM):
    """Non-streaming HTTP implementation for Responses-compatible providers."""

    def __init__(self, *, api_key: str, model: str, base_url: str,
                 timeout: float = 45, max_output_tokens: int = 8192, reasoning_effort: str = "low"):
        endpoint = urlsplit(base_url)
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment):
            raise LLMError("模型 API 地址必须是无用户名、查询参数的 HTTPS 地址。")
        if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in "\r\n"):
            raise LLMError("请先在本地配置 DEEPSEEK_API_KEY。")
        if not isinstance(model, str) or not model.strip():
            raise LLMError("模型名称不能为空。")
        if not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise LLMError("模型请求超时必须为1–120秒。")
        if reasoning_effort not in REASONING_EFFORTS:
            raise LLMError("推理强度只能是" + "/".join(REASONING_EFFORTS) + "。")
        self.reasoning_effort = reasoning_effort
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.endpoint = base_url.rstrip("/") + "/responses"
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def create_response(self, *, input: list[dict], instructions: str,
                        tools: list[dict], tool_choice: str = "auto") -> LLMResponse:
        payload = {"model": self.model, "instructions": instructions, "input": input,
                   "tools": tools, "tool_choice": tool_choice,
                   "max_output_tokens": self.max_output_tokens, "stream": False,
                   "store": False, "reasoning": {"effort": self.reasoning_effort}}
        request = Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False,
                          allow_nan=False).encode("utf-8"), method="POST",
                          headers={"Authorization": "Bearer " + self._api_key,
                                   "Content-Type": "application/json",
                                   "User-Agent": "SocialDogResearchAgent/0.1"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise LLMError("模型响应过大，已停止处理。")
            data = json.loads(body)
        except HTTPError as error:
            descriptions = {400: "模型请求格式不受支持，请检查模型配置。",
                            401: "DeepSeek API Key 无效。", 402: "DeepSeek 账户余额不足。",
                            403: "DeepSeek 拒绝访问，请检查账户权限。",
                            429: "DeepSeek 请求限流，请稍后重试。"}
            raise LLMError(descriptions.get(error.code, f"模型 API 请求失败（HTTP {error.code}）。")) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise LLMError("无法连接模型 API 或请求超时，请检查网络后重试。") from None
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise LLMError("模型 API 返回了无法解析的 JSON。") from None
        return parse_response(data)


class DeepSeekLLM(ResponsesAPILLM):
    def __init__(self, *, api_key: str, model: str = "deepseek-flash", timeout: float = 45,
                 reasoning_effort: str = "low"):
        super().__init__(api_key=api_key, model=model, base_url="https://api.deepseek.com",
                         timeout=timeout, reasoning_effort=reasoning_effort)

    def create_response(self, *, input: list[dict], instructions: str,
                        tools: list[dict], tool_choice: str = "auto") -> LLMResponse:
        # DeepSeek thinking mode rejects forced tool selection. The agent still
        # independently rejects answers without valid tool evidence.
        if self.reasoning_effort != "none" and tool_choice == "required":
            tool_choice = "auto"
        return super().create_response(input=input, instructions=instructions,
                                       tools=tools, tool_choice=tool_choice)


def parse_response(data: Any) -> LLMResponse:
    if not isinstance(data, dict) or data.get("status") != "completed" or data.get("error"):
        raise LLMError("模型响应未完整完成；本次不生成结论，请重试或缩小问题。")
    if not isinstance(data.get("output"), list):
        raise LLMError("模型响应缺少 output 列表。")
    seen = set()
    for item in data["output"]:
        if not isinstance(item, dict):
            raise LLMError("模型响应项格式错误。")
        kind = item.get("type")
        if kind == "function_call":
            if (not all(isinstance(item.get(k), str) and item[k] for k in ["call_id", "name", "arguments"])
                    or item["call_id"] in seen):
                raise LLMError("模型工具调用缺少标识、参数或重复使用 call_id。")
            seen.add(item["call_id"])
        elif kind in {"message", "reasoning"}:
            if not isinstance(item.get("content", []), list):
                raise LLMError("模型文本内容格式错误。")
            for part in item.get("content", []):
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    raise LLMError("模型文本片段格式错误。")
        else:
            raise LLMError("模型返回了未支持的工具或输出类型。")
    return LLMResponse(id=str(data.get("id", "")), model=str(data.get("model", "")),
                       output=data["output"], usage=data.get("usage") or {})
