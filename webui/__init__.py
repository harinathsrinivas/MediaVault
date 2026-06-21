"""MediaVault local web console (IMP-E12).

Package marker. The FastAPI app factory lives in ``webui.server.create_app``.
The server is import-safe and TestClient-friendly: importing this package or
calling ``create_app()`` starts no socket and imports no ``uvicorn`` — the
actual ``uvicorn.run`` lives only in ``main.cmd_web`` (step 5).
"""
