"""PyInstaller entry point for the bundled desktop backend.

This is the frozen counterpart of ``python -m tradingagents.api.server``.
PyInstaller needs a concrete script (not a ``-m`` module invocation) as its
analysis root, so this thin wrapper simply forwards to the real server main.

The Tauri shell (src-tauri/src/main.rs) launches the compiled binary as a
sidecar and injects ``TRADINGAGENTS_API_HOST`` / ``TRADINGAGENTS_API_PORT``.
All data still defaults to ``~/.tradingagents`` so the desktop app shares
state with the CLI and the dev server.
"""

from __future__ import annotations

import multiprocessing


def main() -> None:
    # uvicorn / libraries may spawn processes; required so frozen builds do
    # not re-run the bootstrap in child processes.
    multiprocessing.freeze_support()

    from tradingagents.api.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
