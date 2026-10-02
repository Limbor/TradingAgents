"""Server entry point — run with: python -m tradingagents.api.server"""

import os

import uvicorn

from .app import create_app


def main():
    host = os.environ.get("TRADINGAGENTS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADINGAGENTS_API_PORT", "8422"))

    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
