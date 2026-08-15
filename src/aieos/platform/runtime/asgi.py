"""PED-I06 ASGI server configuration helpers.

NON_PRODUCTION runtime foundation only. Importing this module must not start
a server, open a database connection, load environment config, construct the
API application, or perform network I/O.
"""

from __future__ import annotations

from typing import Any

import uvicorn


def create_uvicorn_config(app: Any) -> uvicorn.Config:
    """Build the frozen PED-I06 Uvicorn Config for a provided ASGI app.

    Does not start the server. Does not construct a product application.
    """
    return uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8080,
        workers=1,
        loop="asyncio",
        http="h11",
        proxy_headers=False,
        server_header=False,
        reload=False,
        lifespan="on",
    )


def serve_api_application(app: Any) -> None:
    """Serve a caller-provided ASGI application with PED-I06 defaults.

    The caller must supply the application; this helper does not compose one.
    """
    config = create_uvicorn_config(app)
    server = uvicorn.Server(config)
    server.run()
