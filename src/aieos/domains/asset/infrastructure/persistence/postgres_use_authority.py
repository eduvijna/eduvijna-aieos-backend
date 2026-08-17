"""PostgreSQL Asset current-use authority composition (PED-I10B4).

Not a production BlobStore. Not composed into API runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.engine import Engine

from aieos.domains.asset.application.blob_store import BlobStore
from aieos.domains.asset.application.use_authority import AssetCurrentUseAuthority
from aieos.domains.asset.infrastructure.persistence.authority_reads import (
    PostgresAssetCurrentUseStore,
)


class PostgresAssetUseAuthority(AssetCurrentUseAuthority):
    """AssetUseAuthority backed by Asset PostgreSQL SoR + provider-neutral BlobStore."""

    def __init__(
        self,
        engine: Engine,
        blob_store: BlobStore,
        *,
        clock: Callable[[], datetime] | None = None,
        max_positive_attempts: int = 3,
    ) -> None:
        super().__init__(
            PostgresAssetCurrentUseStore(engine),
            blob_store,
            clock=clock,
            max_positive_attempts=max_positive_attempts,
        )
