"""Launch the SocialDog research chat on loopback, using the existing agent."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser
import json

import chat_agent
import research_assistant as research
from socialdog_agent import ResearchAgent, build_history_tools, build_research_tools
from socialdog_agent.history_tools import DEFAULT_MIN_ORDERS
from socialdog_agent.feishu_source import FeishuSnapshotSource, DEFAULT_SYNC_STATE
from socialdog_agent.web import ChatService, LocalServer


def make_service(state: Path = research.DEFAULT_STATE, sync_state: Path = DEFAULT_SYNC_STATE,
                 history_package: Path | None = None, history_min_orders: int = DEFAULT_MIN_ORDERS) -> ChatService:
    source = FeishuSnapshotSource(state=state, sync_state=sync_state)

    def prepare_tools():
        if history_package is not None:
            return build_history_tools(history_package, state, history_min_orders)
        snapshot, info = source.resolve()
        return build_research_tools(snapshot, state, info)

    def status():
        settings = chat_agent.load_settings()
        configured = bool(settings.get("DEEPSEEK_API_KEY", "").strip())
        if history_package is not None:
            info = prepare_tools().source_info
            version, start, end = info["dataset_version"], None, info["source_data_as_of"]
        else:
            snapshot, info = source.resolve()
            manifest, connection = research.read_snapshot(snapshot)
            connection.close()
            version, start, end = manifest["snapshot_version"], manifest.get("observation_start"), manifest.get("last_order_at")
        return {"ready": configured, "api_key_configured": configured, "model": settings["DEEPSEEK_MODEL"],
                "data_source": info, "snapshot_version": version,
                "observation_start": start, "observation_end": end,
                "error": None if configured else "请在本机终端运行 chat_agent.py --setup 配置DeepSeek API Key。"}

    def agent_factory():
        return ResearchAgent(chat_agent.make_llm(chat_agent.load_settings()), state=state, tool_factory=prepare_tools)

    return ChatService(state, agent_factory, status)


def main() -> int:
    parser = argparse.ArgumentParser(description="SocialDog本地研究聊天窗口")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=research.DEFAULT_STATE)
    parser.add_argument("--sync-state", type=Path, default=DEFAULT_SYNC_STATE)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--history-package", type=Path, help="历史数据模式：使用离线回测包，不读取飞书")
    parser.add_argument("--history-min-orders", type=int, default=DEFAULT_MIN_ORDERS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        print("端口必须为1024–65535。", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{args.port}"
    # Reuse an already-running instance instead of resetting its pending jobs.
    try:
        with urlopen(url + "/api/status", timeout=2) as response:
            existing = json.load(response)
        if existing.get("app") == "socialdog-local-research":
            print("SocialDog网页窗口已运行：" + url, flush=True)
            if not args.no_open:
                webbrowser.open(url)
            return 0
    except (URLError, OSError, ValueError):
        pass
    try:
        service = make_service(args.state_dir, args.sync_state, args.history_package, args.history_min_orders)
        server = LocalServer(args.port, service)
    except OSError:
        print("本地端口被占用或会话目录不可用，请更换端口或检查磁盘。", file=sys.stderr)
        return 1
    print("SocialDog网页窗口：" + server.origin, flush=True)
    print("仅监听本机。关闭服务后可重新启动，会话记录保存在本地。按Ctrl+C退出。", flush=True)
    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(server.origin)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n网页服务已退出。", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
