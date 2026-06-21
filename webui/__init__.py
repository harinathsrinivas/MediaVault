"""MediaVault local web console (IMP-E12).

Package marker for the FastAPI operations/console UI. The app factory lives in
``webui.server`` (``create_app()``). The CLI entrypoint (``cmd_web``) that binds
uvicorn to localhost is added in a later step — this package stays import-safe
and never imports uvicorn at module top.
"""
