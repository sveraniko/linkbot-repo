"""MinIO helper with ensure_bucket + public URL."""
from __future__ import annotations
import uuid
import io
import os
import logging
from typing import Optional
from urllib.parse import urljoin
from minio import Minio
from app.config import settings

logger = logging.getLogger(__name__)
_client: Optional[Minio] = None

def _client_or_none() -> Optional[Minio]:
    """Get MinIO client with lazy initialization, returns None if not configured."""
    global _client
    if _client:
        return _client
    if not settings.minio_endpoint:
        return None
    
    try:
        endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
        _client = Minio(
            endpoint,
            access_key=settings.minio_access_key or "",
            secret_key=settings.minio_secret_key or "",
            secure=bool(settings.minio_secure),
        )
        return _client
    except Exception as e:
        logger.warning(f"Failed to initialize MinIO client: {e}")
        return None

async def ensure_bucket() -> None:
    """Ensure that the configured bucket exists."""
    c = _client_or_none()
    if not c:
        return
    
    try:
        bucket = settings.minio_bucket
        if not c.bucket_exists(bucket):
            c.make_bucket(bucket)
    except Exception as e:
        logger.warning(f"Failed to ensure MinIO bucket: {e}")

async def save_file(filename: str, data: bytes) -> str | None:
    """Save file to MinIO and return public URL, or None if MinIO not configured."""
    c = _client_or_none()
    if not c:
        return None
    
    try:
        await ensure_bucket()
        key = f"{uuid.uuid4()}-{filename}"
        c.put_object(settings.minio_bucket, key, io.BytesIO(data), length=len(data))
        
        # Generate public URL using MINIO_PUBLIC_URL or fallback to endpoint
        public_base = os.getenv("MINIO_PUBLIC_URL") or settings.minio_endpoint or ""
        if public_base:
            return urljoin(f"{public_base.rstrip('/')}/", f"{settings.minio_bucket}/{key}")
        return None
    except Exception as e:
        logger.warning(f"Failed to save file to MinIO: {e}")
        return None

async def load_file(key: str) -> Optional[bytes]:
    """Load file from MinIO by key extracted from URL."""
    c = _client_or_none()
    if not c:
        return None
    
    try:
        # Extract key from URL if needed
        if "/" in key:
            key = key.split("/")[-1]  # Get last part as key
        
        response = c.get_object(settings.minio_bucket, key)
        data = response.read()
        response.close()
        response.release_conn()
        return data
    except Exception as e:
        logger.warning(f"Failed to load file from MinIO: {e}")
        return None

async def delete_file(key: str) -> bool:
    """Delete file from MinIO."""
    c = _client_or_none()
    if not c:
        return False
    
    try:
        # Extract key from URL if needed
        if "/" in key:
            key = key.split("/")[-1]
        
        c.remove_object(settings.minio_bucket, key)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete file from MinIO: {e}")
        return False