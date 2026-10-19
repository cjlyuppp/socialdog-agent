"""CLI entry point: configure, check, or ask the DeepSeek research agent."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys

import research_assistant as research
from socialdog_agent import DeepSeekLLM, ResearchAgent, build_research_tools
from socialdog_agent.llm import LLMError
from socialdog_agent.feishu_source import FeishuSnapshotSource, DEFAULT_SYNC_STATE

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_FIELDS = {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_TIMEOUT"}


def load_settings(path: Path = ENV_FILE) -> dict:
    settings = {"DEEPSEEK_MODEL": "deepseek-flash", "DEEPSEEK_TIMEOUT": "45"}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator or name.strip() not in ENV_FIELDS:
                raise LLMError(".env格式不正确；仅支持示例中的DeepSeek配置，不能包含Shell命令。")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            settings[name.strip()] = value
    for name in ENV_FIELDS:
        if name in os.environ:
            settings[name] = os.environ[name]
    return settings


def configure() -> None:
    if not sys.stdin.isatty():
        raise LLMError("请在本机终端运行 --setup，以隐藏输入API Key。")
    key = getpass.getpass("DeepSeek API Key（输入不显示，保存在本地.env）：").strip()
    if not key or any(c.isspace() for c in key):
        raise LLMError("API Key不能为空或包含空白字符。")
    settings = load_settings()
    contents = (f"DEEPSEEK_API_KEY={key}\nDEEPSEEK_MODEL={settings['DEEPSEEK_MODEL']}\n"
                f"DEEPSEEK_TIMEOUT={settings['DEEPSEEK_TIMEOUT']}\n")
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(contents)
    ENV_FILE.chmod(0o600)
    print("配置已保存。API Key不会写入分析日志。")


def print_answer(result: dict, show_evidence: bool = False) -> None:
    print(result["answer"])
    print("\n[复核] 快照：" + ("、".join(result["snapshot_versions"]) or "无可用快照"))
    print("工具：" + " → ".join(e["tool"] for e in result["evidence"]))
    print("审计编号：" + result["request_id"])
    source = result.get("data_source")
    if source:
        print("飞书来源核验：" + source["source_checked_at"])
        print("来源数据截至：" + source["source_data_as_of"])
        if source["freshness_status"] == "check_overdue":
            print("注意：最近核验已超过每日检查窗口，尚未确认飞书是否有新变化。")
    if show_evidence:
        print(research.encoded(result["evidence"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="SocialDog研究助理：DeepSeek Responses API")
    parser.add_argument("--snapshot", type=Path, help="显式使用固定历史快照；默认使用飞书最新同步版本")
    parser.add_argument("--sync-state", type=Path, default=DEFAULT_SYNC_STATE,
                        help="已有飞书每日同步状态文件")
    parser.add_argument("--state-dir", type=Path, default=research.DEFAULT_STATE)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--setup", action="store_true", help="在本地隐藏输入API Key")
    modes.add_argument("--check", action="store_true", help="检查本地配置与快照，不调用API")
    modes.add_argument("--question", help="单次中文提问；不指定则进入交互模式")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果与工具证据")
    parser.add_argument("--show-evidence", action="store_true", help="显示模型收到的工具结果")
    args = parser.parse_args()
    try:
        if args.setup:
            configure()
            return 0
        settings = load_settings()
        source = FeishuSnapshotSource(state=args.state_dir, sync_state=args.sync_state)

        def prepare_tools():
            if args.snapshot is not None:
                return build_research_tools(args.snapshot, args.state_dir)
            path, info = source.resolve()
            return build_research_tools(path, args.state_dir, info)

        if args.snapshot is not None:
            snapshot, source_info = args.snapshot, {}
        else:
            snapshot, source_info = source.resolve()
        manifest, connection = research.read_snapshot(snapshot)
        connection.close()
        if args.check:
            configured = bool(settings.get("DEEPSEEK_API_KEY", "").strip())
            print(research.encoded({"snapshot_ok": True, "snapshot_version": manifest["snapshot_version"],
                  "api_key_configured": configured, "model": settings["DEEPSEEK_MODEL"],
                  "endpoint": "https://api.deepseek.com/responses", "network_called": False,
                  "data_source": source_info}))
            return 0 if configured else 2
        try:
            timeout = float(settings["DEEPSEEK_TIMEOUT"])
        except ValueError:
            raise LLMError("DEEPSEEK_TIMEOUT必须为秒数。") from None
        agent = ResearchAgent(DeepSeekLLM(api_key=settings.get("DEEPSEEK_API_KEY", ""),
                              model=settings["DEEPSEEK_MODEL"], timeout=timeout),
                              state=args.state_dir, tool_factory=prepare_tools)
        if args.question is not None:
            answer = agent.ask(args.question)
            print(research.encoded(answer)) if args.json else print_answer(answer, args.show_evidence)
            return 0
        mode = "每次提问读取最新成功同步的飞书版本" if args.snapshot is None else "固定历史快照模式"
        print(f"SocialDog研究助理已启动。{mode}；请输入完整日期与范围。输入 /exit 退出。")
        while True:
            try:
                question = input("\n你：").strip()
            except EOFError:
                break
            if question.lower() in {"/exit", "/quit"}:
                break
            if not question:
                continue
            try:
                answer = agent.ask(question)
                print(research.encoded(answer)) if args.json else print_answer(answer, args.show_evidence)
            except (LLMError, ValueError) as error:
                print("本次分析未完成：" + str(error), file=sys.stderr)
        return 0
    except (LLMError, ValueError, OSError, KeyError, research.AnalysisError):
        # Specific known errors are safe; file/JSON errors should not expose paths or credentials.
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, (LLMError, research.AnalysisError)) else "本地配置或快照不可用，请检查 --check。"
        if args.json:
            print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已退出。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
