"""Production HTTP API executable entrypoint."""

from __future__ import annotations

import logging
import sys
from typing import Sequence

from aieos.platform.runtime.activation import MutationRouteClassificationError
from aieos.platform.runtime.asgi import serve_api_application
from aieos.platform.runtime.compose_api_dependencies import compose_api_runtime_dependencies
from aieos.platform.runtime.composition import compose_api_application
from aieos.platform.runtime.config import load_api_runtime_config_from_process_environment
from aieos.platform.runtime.database import create_api_runtime_engine
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import WorkloadKind

logger = logging.getLogger(__name__)


def _configure_logging(config) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "api startup workload=%s environment=%s git_sha=%s",
        WorkloadKind.API.value,
        config.environment.value,
        config.release_identity.git_sha,
    )


def _log_startup_failure(category: str, exc: BaseException) -> None:
    logger.error(
        "api startup failed category=%s error_type=%s",
        category,
        type(exc).__name__,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Load config, compose the API, serve until shutdown, dispose the Engine."""
    _ = argv
    engine = None
    try:
        config = load_api_runtime_config_from_process_environment()
        _configure_logging(config)
        engine = create_api_runtime_engine(config)
        dependencies = compose_api_runtime_dependencies(engine=engine, config=config)
        app = compose_api_application(config, dependencies)
        serve_api_application(app)
    except RuntimeConfigurationError as exc:
        _log_startup_failure("configuration", exc)
        sys.exit(1)
    except MutationRouteClassificationError as exc:
        _log_startup_failure("mutation_route_classification", exc)
        sys.exit(1)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _log_startup_failure("composition", exc)
        sys.exit(1)
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
