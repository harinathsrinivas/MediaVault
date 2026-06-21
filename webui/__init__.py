"""MediaVault local web operations console (IMP-E12).

Package marker. The FastAPI app factory lives in ``webui.server.create_app``;
the served single-page UI lives under ``webui/static``. Importing this package
has no side effects (no server is started here — ``cmd_web`` in main.py owns
uvicorn).
"""
