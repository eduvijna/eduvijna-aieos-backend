"""Asset application ports: BlobStore, ingest, reconciliation, current-use, mutations.

No SQLAlchemy, FastAPI, Temporal, NATS, Content services, or provider SDKs.
No production BlobStore adapter. AssetUseAuthority Protocol stays on the platform.
Mutation services are NON_PRODUCTION foundations and are not runtime-composed.
"""

from aieos.domains.asset.application.audit import (
    AssetMutationAuditProvenance,
    asset_pinned_revision_ref,
    asset_stable_primary_ref,
    insert_required_asset_audit,
)
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
from aieos.domains.asset.application.mutation_errors import (
    AssetActivationRejected,
    AssetApplicationError,
    AssetConflict,
    AssetForbidden,
    AssetIdentityConflict,
    AssetNotFound,
    AssetPersistenceFailed,
    AssetTransitionRejected,
)
from aieos.domains.asset.application.mutations import (
    AssetMutationService,
    RegisteredRevision,
)
from aieos.domains.asset.application.ports import (
    ASSET_CREATE,
    ASSET_LIFECYCLE_MANAGE,
    ASSET_QUARANTINE_MANAGE,
    ASSET_REVISION_ACTIVATE,
    ASSET_REVISION_REGISTER,
    ASSET_SAFETY_DECIDE,
    AssetMutationAuthorizationPort,
    AssetRevisionStateWriteRepository,
    AssetRevisionWriteRepository,
    AssetUnitOfWork,
    AssetUnitOfWorkFactory,
    AssetWriteRepository,
)
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
    "ASSET_CREATE",
    "ASSET_LIFECYCLE_MANAGE",
    "ASSET_QUARANTINE_MANAGE",
    "ASSET_REVISION_ACTIVATE",
    "ASSET_REVISION_REGISTER",
    "ASSET_SAFETY_DECIDE",
    "AssetActivationRejected",
    "AssetApplicationError",
    "AssetConflict",
    "AssetCurrentUseAuthority",
    "AssetCurrentUseStore",
    "AssetForbidden",
    "AssetIdentityConflict",
    "AssetIdentityFacts",
    "AssetMutationAuditProvenance",
    "AssetMutationAuthorizationPort",
    "AssetMutationService",
    "AssetNotFound",
    "AssetPersistenceFailed",
    "AssetRevisionStateWriteRepository",
    "AssetRevisionWriteRepository",
    "AssetTransitionRejected",
    "AssetUnitOfWork",
    "AssetUnitOfWorkFactory",
    "AssetWriteRepository",
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
    "RegisteredRevision",
    "RevisionFacts",
    "RevisionStateFacts",
    "StorageKeyFactory",
    "Uuid7StorageKeyFactory",
    "asset_pinned_revision_ref",
    "asset_stable_primary_ref",
    "insert_required_asset_audit",
]
