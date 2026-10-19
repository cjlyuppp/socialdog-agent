"""Run and administer the authenticated research workspace on loopback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import research_assistant as research


def parser():
    result = argparse.ArgumentParser(description="SocialDog 多人研究工作台")
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 FastAPI 后端及已构建的前端")
    serve.add_argument("--history-package", type=Path, required=True)
    serve.add_argument("--reference-db", type=Path)
    serve.add_argument("--question-bank", type=Path)
    serve.add_argument("--database", type=Path, default=research.DEFAULT_STATE / "workspace.sqlite")
    serve.add_argument("--state-dir", type=Path, default=research.DEFAULT_STATE / "workspace-audit")
    serve.add_argument("--public-origin", default="https://localhost")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--frontend-dir", type=Path, default=Path("frontend/dist"))
    serve.add_argument("--local-http", action="store_true", help="仅用于本机 HTTP；允许非 Secure cookie")
    bootstrap = commands.add_parser("init-admin", help="仅在空数据库创建首位管理员的一次性邀请")
    bootstrap.add_argument("--database", type=Path, default=research.DEFAULT_STATE / "workspace.sqlite")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--public-origin", required=True)
    reference = commands.add_parser("build-reference", help="从完整回测包构建独立数据核对库")
    reference.add_argument("--package", type=Path, required=True)
    reference.add_argument("--output", type=Path, required=True)
    reference.add_argument("--cutoff", required=True)
    schema = commands.add_parser("openapi", help="导出实际服务的 OpenAPI 契约")
    schema.add_argument("--history-package", type=Path, required=True)
    schema.add_argument("--output", type=Path, required=True)
    return result


def main():
    args = parser().parse_args()
    try:
        if args.command == "build-reference":
            from socialdog_agent.server.data import build_reference
            build_reference(args.package, args.output, args.cutoff)
            print("数据核对库已生成。")
            return 0
        if args.command == "init-admin":
            from socialdog_agent.server.app import ServerConfig
            from socialdog_agent.server.store import Store
            # Reuse origin validation; no password is accepted through argv or logs.
            ServerConfig(args.database, args.database.parent, public_origin=args.public_origin)
            token, expires = Store(args.database, "1970-01-01").bootstrap(args.username, args.display_name)
            print(json.dumps({"invite_url": args.public_origin.rstrip("/") + "/invite/" + token, "expires_at": expires}, ensure_ascii=False))
            return 0
        from socialdog_agent.server.app import ServerConfig, create_app
        if args.command == "openapi":
            import tempfile
            with tempfile.TemporaryDirectory() as temporary:
                app = create_app(ServerConfig(Path(temporary) / "schema.sqlite", Path(temporary), history_package=args.history_package))
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("OpenAPI 契约已导出。")
            return 0
        if not 1024 <= args.port <= 65535:
            raise ValueError("端口必须为 1024–65535")
        app = create_app(ServerConfig(args.database, args.state_dir, public_origin=args.public_origin,
                                     history_package=args.history_package, reference_db=args.reference_db,
                                     question_bank=args.question_bank, secure_cookie=not args.local_http,
                                     static_dir=args.frontend_dir))
        import uvicorn
        # Deploy behind an HTTPS reverse proxy; bind loopback and run a single owner.
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, proxy_headers=False, access_log=False)
        return 0
    except (ValueError, OSError, KeyError) as error:
        print("工作台启动失败，请检查参数、数据包和目录权限。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
