"""CLI entry point for running the Vibe-Trading API server.

Extracted from ``api_server.py`` (the thin assembler) to keep that module
under its enforced line-count ceiling — see
``tests/test_api_infrastructure.py::test_api_server_is_thin_assembler``.

This module is imported lazily by ``api_server.py`` at the bottom of that
file and re-exported as ``api_server.serve_main`` for external callers
(``cli/_legacy.py``, the desktop Electron backend manager, and
``tests/test_serve_bind.py`` all call ``api_server.serve_main`` directly).

Everything here references the *host* ``api_server`` module (its ``app``
and its ``__file__``) rather than this module's own ``__file__`` — several
tests monkeypatch ``api_server.__file__``/``api_server.app`` and expect
``serve_main`` to observe the patched host state, the same
``sys.modules["api_server"]`` convention used by ``src.api._compat``.
"""

from __future__ import annotations


def serve_main(argv: list[str] | None = None) -> int:
    """Start the API server from CLI-style arguments."""
    import argparse
    import subprocess
    import sys
    from pathlib import Path

    import uvicorn

    import api_server as _host
    from src.api.helpers import SPAStaticFiles
    from src.api.security import (
        _configured_api_key,
        _is_loopback_bind_host,
        install_access_log_redaction_filter,
    )

    app = _host.app

    parser = argparse.ArgumentParser(description="Vibe-Trading Server")
    parser.add_argument("--port", type=int, default=8000, help="Listen port (default 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--dev", action="store_true", help="Dev mode: spawn Vite on :5173")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-restart on backend code changes (this file's dir + the monorepo integrations/ tree)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.service_lock import acquire_service_lock

        acquire_service_lock("vibetrading-agent", port=args.port)
    except (ImportError, RuntimeError):
        pass  # trade_integrations not on path (standalone vibe-trading install)

    if not _is_loopback_bind_host(args.host) and not _configured_api_key():
        print(
            f"[warn] Binding to {args.host} without API_AUTH_KEY set. "
            f"Remote requests are rejected by the loopback peer-IP check, "
            f"but consider using --host 127.0.0.1 for local-only access."
        )

    frontend_dist = Path(_host.__file__).resolve().parent.parent / "frontend" / "dist"
    frontend_root = Path(_host.__file__).resolve().parent.parent / "frontend"

    vite_proc = None
    if args.dev and frontend_root.exists():
        print("[dev] Starting Vite dev server on :5173 ...")
        vite_proc = subprocess.Popen(
            ["npx", "vite", "--host", "0.0.0.0"],
            cwd=str(frontend_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[dev] Vite PID={vite_proc.pid}")
        print("[dev] Frontend: http://localhost:5173")
        print(f"[dev] API: http://localhost:{args.port}")
    elif frontend_dist.exists():
        if not any(getattr(route, "path", None) == "/" for route in app.routes):
            app.mount("/", SPAStaticFiles(directory=str(frontend_dist), html=True), name="frontend")
        print(f"[prod] Frontend served from {frontend_dist}")
    else:
        print(f"[warn] No frontend build found at {frontend_dist}")
        print("[warn] Run: cd frontend && npm run build")

    print("=" * 50)
    print("  Vibe-Trading Server")
    print(f"  http://127.0.0.1:{args.port}")
    print("=" * 50)

    # Redact api_key=/ticket= values from Uvicorn's access log (it logs the full
    # request line including the query string). Installed before run() so the
    # filter is attached when Uvicorn configures its loggers.
    install_access_log_redaction_filter()

    try:
        if args.reload:
            agent_dir = Path(_host.__file__).resolve().parent
            reload_dirs = [str(agent_dir)]
            # Mirror hub_bridge.ensure_trade_stack_path()'s sys.path insertions exactly —
            # those are the directories this process actually imports live code from
            # (trade_integrations under integrations/, plus tradingagents/), so anything
            # not covered here can change on disk without the reloader ever noticing.
            try:
                from src.trade.hub_bridge import trade_repo_root

                repo_root = trade_repo_root()
            except Exception:
                repo_root = None
            if repo_root is not None:
                for extra in (repo_root / "integrations", repo_root / "tradingagents"):
                    if extra.is_dir():
                        reload_dirs.append(str(extra))
            print(f"[dev] Auto-reload watching: {', '.join(reload_dirs)}")
            # uvicorn.run(..., reload=True) spawns workers via multiprocessing's "spawn"
            # context, which needs to re-import whatever module was recorded as __main__ —
            # here that's the dotted `cli._legacy`, which spawn's bootstrap cannot resolve
            # (ImportError: No module named cli._legacy), confirmed live. Running uvicorn's
            # own CLI as a subprocess sidesteps this entirely: __main__ becomes uvicorn's,
            # its own officially-supported reload invocation.
            reload_dir_args = []
            for directory in reload_dirs:
                reload_dir_args.extend(["--reload-dir", directory])
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "api_server:app",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--log-level",
                "info",
                "--reload",
                # Without this, uvicorn's graceful shutdown on each reload
                # waits forever (asyncio.wait_for(..., timeout=None)) for
                # every open connection to close on its own — see
                # uvicorn/server.py's Server.shutdown()/_wait_tasks_to_complete().
                # A long-lived SSE stream (e.g. /trade/command-center/stream)
                # never closes itself, so every reload after one of those
                # connects hangs at "Waiting for connections to close." and
                # never comes back up. This bounds the wait so uvicorn cancels
                # the stuck connection's task and finishes the reload instead
                # of hanging indefinitely; a real in-flight request has a few
                # seconds to finish normally first.
                "--timeout-graceful-shutdown",
                "3",
                *reload_dir_args,
            ]
            subprocess.run(cmd, cwd=str(agent_dir))
        else:
            # Same reasoning as the --reload branch's --timeout-graceful-shutdown
            # above: uvicorn's default (None) waits forever for every open
            # connection to close on its own, and a long-lived SSE stream (e.g.
            # /trade/command-center/stream) never does — this branch is what
            # `trade heal`/`trade up` actually launch (no --reload), and hung
            # exactly this way twice live during 2026-08-30 scheduler testing:
            # alive process, no listening socket, requiring a hard kill.
            uvicorn.run(
                app, host=args.host, port=args.port, log_level="info",
                timeout_graceful_shutdown=3,
            )
    finally:
        if vite_proc:
            vite_proc.terminate()
            print("[dev] Vite stopped")
    return 0
