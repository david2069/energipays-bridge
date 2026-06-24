"""energipays-bridge CLI entry point."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="energipays-bridge")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="start the web server")
    run_p.add_argument("--host", default=None)
    run_p.add_argument("--port", type=int, default=None)
    run_p.add_argument("--reload", action="store_true", help="enable auto-reload (dev)")

    args = p.parse_args(argv)

    if args.command == "run":
        import uvicorn
        from .config.settings import BridgeSettings
        settings = BridgeSettings()
        uvicorn.run(
            "energipays_bridge.main:app",
            host=args.host or settings.admin_host,
            port=args.port or settings.admin_port,
            reload=args.reload,
            log_level=settings.log_level.lower(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
