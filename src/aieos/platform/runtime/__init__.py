"""Production/staging API runtime configuration, readiness, and activation.

PED-I01 configuration + PED-I02 database/readiness + PED-I03 mutation
activation safety interlock. Not production-approved.
"""

from __future__ import annotations

from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    ApiMutationActivationGate,
    ConfiguredApiMutationActivationGate,
    MutationActivationDecision,
    MutationActivationStatus,
    load_api_mutation_activation_gate,
    load_api_mutation_activation_gate_from_process_environment,
)
from aieos.platform.runtime.composition import (
    ApiRuntimeDependencies,
    compose_api_application,
)
from aieos.platform.runtime.config import (
    load_api_runtime_config,
    load_api_runtime_config_from_process_environment,
)
from aieos.platform.runtime.database import create_api_runtime_engine
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import (
    ApiRuntimeConfig,
    DeploymentEnvironment,
    ReleaseIdentity,
    WorkloadKind,
)
from aieos.platform.runtime.readiness import (
    EXPECTED_ALEMBIC_HEAD,
    EXPECTED_POSTGRES_MAJOR,
    ApiReadinessProbe,
    ReadinessCode,
    ReadinessResult,
    SqlAlchemyApiReadinessProbe,
)

__all__ = [
    "EXPECTED_ALEMBIC_HEAD",
    "EXPECTED_POSTGRES_MAJOR",
    "FROZEN_API_MUTATION_OPERATION_IDS",
    "ApiMutationActivationGate",
    "ApiReadinessProbe",
    "ApiRuntimeConfig",
    "ApiRuntimeDependencies",
    "ConfiguredApiMutationActivationGate",
    "DeploymentEnvironment",
    "MutationActivationDecision",
    "MutationActivationStatus",
    "ReadinessCode",
    "ReadinessResult",
    "ReleaseIdentity",
    "RuntimeConfigurationError",
    "SqlAlchemyApiReadinessProbe",
    "WorkloadKind",
    "compose_api_application",
    "create_api_runtime_engine",
    "load_api_mutation_activation_gate",
    "load_api_mutation_activation_gate_from_process_environment",
    "load_api_runtime_config",
    "load_api_runtime_config_from_process_environment",
]
