"""Production/staging API runtime configuration and composition (PED-I01).

Configuration and composition foundation only. Not production-ready,
not deployable, and not a mutation-activation gate.
"""

from __future__ import annotations

from aieos.platform.runtime.composition import (
    ApiRuntimeDependencies,
    compose_api_application,
)
from aieos.platform.runtime.config import (
    load_api_runtime_config,
    load_api_runtime_config_from_process_environment,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import (
    ApiRuntimeConfig,
    DeploymentEnvironment,
    ReleaseIdentity,
    WorkloadKind,
)

__all__ = [
    "ApiRuntimeConfig",
    "ApiRuntimeDependencies",
    "DeploymentEnvironment",
    "ReleaseIdentity",
    "RuntimeConfigurationError",
    "WorkloadKind",
    "compose_api_application",
    "load_api_runtime_config",
    "load_api_runtime_config_from_process_environment",
]
