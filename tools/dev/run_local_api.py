"""Local-only FastAPI launcher for Cursor F5 debugging.

Uses production composition paths with explicitly local adapters.
Must not be referenced from production entrypoints.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn

from aieos.platform.runtime.composition import compose_api_application
from aieos.platform.runtime.config import load_api_runtime_config
from aieos.platform.runtime.database import create_api_runtime_engine
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import WorkloadKind
from tools.dev.compose_local_api import compose_local_api_runtime_dependencies
from tools.dev.local_config import (
    API_BIND_HOST,
    API_BIND_PORT,
    apply_local_api_environ,
    is_local_worktree_dirty,
)

logger = logging.getLogger(__name__)

ENV_ALLOW_LAN = "AIEOS_LOCAL_DEV_ALLOW_LAN"


def _configure_logging(config) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "local api startup workload=%s environment=%s git_sha=%s bind=%s:%s",
        WorkloadKind.API.value,
        config.environment.value,
        config.release_identity.git_sha,
        API_BIND_HOST,
        API_BIND_PORT,
    )


def resolve_bind_host(requested: str | None = None) -> str:
    host = API_BIND_HOST if requested is None else requested.strip()
    if host in {"127.0.0.1", "localhost"}:
        return host
    if os.environ.get(ENV_ALLOW_LAN, "").strip() == "1":
        return host
    raise RuntimeConfigurationError(
        f"local debug bind host must be loopback (127.0.0.1/localhost); "
        f"set {ENV_ALLOW_LAN}=1 to override explicitly"
    )


def serve_local_api_application(app) -> None:
    """Serve on loopback only with reload disabled (single debug process)."""
    host = resolve_bind_host()
    config = uvicorn.Config(
        app=app,
        host=host,
        port=API_BIND_PORT,
        workers=1,
        loop="asyncio",
        http="h11",
        proxy_headers=False,
        server_header=False,
        reload=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server.run()


def main(argv: Sequence[str] | None = None) -> None:
    _ = argv
    engine = None
    try:
        _, source_sha = apply_local_api_environ()
        if is_local_worktree_dirty():
            logger.warning("LOCAL DEVELOPMENT WORKTREE DIRTY git_sha=%s", source_sha)
        config = load_api_runtime_config(os.environ)
        _configure_logging(config)
        engine = create_api_runtime_engine(config)
        dependencies = compose_local_api_runtime_dependencies(
            engine=engine,
            config=config,
        )
        app = compose_api_application(config, dependencies)
        serve_local_api_application(app)
    except RuntimeConfigurationError as exc:
        logger.error("local api startup failed category=configuration error=%s", exc)
        sys.exit(1)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        logger.error(
            "local api startup failed category=composition error_type=%s",
            type(exc).__name__,
        )
        sys.exit(1)
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
