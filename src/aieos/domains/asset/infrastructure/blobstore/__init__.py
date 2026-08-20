"""Asset BlobStore infrastructure (MinIO AIStor adapter).

Provider SDK imports are permitted only inside this package.
"""

from aieos.domains.asset.infrastructure.blobstore.aistor import AiStorBlobStore
from aieos.domains.asset.infrastructure.blobstore.config import AiStorBlobStoreConfig

__all__ = ["AiStorBlobStore", "AiStorBlobStoreConfig"]
