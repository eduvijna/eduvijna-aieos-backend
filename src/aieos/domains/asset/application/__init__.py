"""Asset application ports: BlobStore, ingest, reconciliation, current-use (PED-I10B4).

No SQLAlchemy, FastAPI, Temporal, NATS, Content services, or provider SDKs.
No production BlobStore adapter. AssetUseAuthority Protocol stays on the platform.
"""

from aieos.domains.asset.application.blob_store import (
    BlobInventory,
    BlobObjectInfo,
    BlobStore,
    ReadableBinary,
    StorageKeyFactory,
    Uuid7StorageKeyFactory,
)
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    BlobStoreContractError,
    BlobStoreError,
    BlobStoreUnavailableError,
    ConflictingBlobReferenceError,
    InvalidBlobInventoryError,
    InvalidBlobObjectInfoError,
    InvalidBlobReferenceError,
)
from aieos.domains.asset.application.ingest import BlobIngestPreparer, PreparedBlob
from aieos.domains.asset.application.reconciliation import (
    AuthoritativeBlobReference,
    AuthoritativeBlobReferenceSource,
    BlobReconciler,
    BlobReconciliationReport,
    BlobReferenceCheck,
    BlobReferenceStatus,
    OrphanBlobCandidate,
)
from aieos.domains.asset.application.use_authority import (
    AssetCurrentUseAuthority,
    AssetCurrentUseStore,
    AssetIdentityFacts,
    GoverningSnapshot,
    RevisionFacts,
    RevisionStateFacts,
)

__all__ = [
    "AssetCurrentUseAuthority",
    "AssetCurrentUseStore",
    "AssetIdentityFacts",
    "AuthoritativeBlobReference",
    "AuthoritativeBlobReferenceSource",
    "BlobAlreadyExistsError",
    "BlobIngestPreparer",
    "BlobInventory",
    "BlobObjectInfo",
    "BlobReconciler",
    "BlobReconciliationReport",
    "BlobReferenceCheck",
    "BlobReferenceStatus",
    "BlobStore",
    "BlobStoreContractError",
    "BlobStoreError",
    "BlobStoreUnavailableError",
    "ConflictingBlobReferenceError",
    "GoverningSnapshot",
    "InvalidBlobInventoryError",
    "InvalidBlobObjectInfoError",
    "InvalidBlobReferenceError",
    "OrphanBlobCandidate",
    "PreparedBlob",
    "ReadableBinary",
    "RevisionFacts",
    "RevisionStateFacts",
    "StorageKeyFactory",
    "Uuid7StorageKeyFactory",
]
