import sqlite3
import logging
import httpx
import base64
import json
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class ImageCache(ABC):
    """Abstract base class for image caching with SQLite storage"""

    def __init__(
        self,
        db_path: str = "image_cache.db",
        expiration_days: int = 7,
    ):
        self.db_path = db_path
        self.expiration_days = expiration_days
        self.client: Optional[httpx.AsyncClient] = None
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database schema"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS image_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    entity_type TEXT NOT NULL,
                    cached_url TEXT NOT NULL,
                    expiration_date TIMESTAMP NOT NULL,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_id, entity_type)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity
                ON image_cache(entity_id, entity_type)
            """)
            conn.commit()
            conn.close()
            logger.debug(f"Image cache database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Error initializing image cache database: {e}")

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def _get_cached_url_from_db(self, entity_id: int, entity_type: str) -> Optional[str]:
        """Check if entity's image is already cached and not expired"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT cached_url FROM image_cache
                WHERE entity_id = ? AND entity_type = ?
                AND expiration_date > ?
            """, (entity_id, entity_type, datetime.now()))
            result = cursor.fetchone()
            conn.close()
            if result:
                logger.debug(f"Found cached URL for {entity_type}:{entity_id}")
                return result[0]
            return None
        except Exception as e:
            logger.debug(f"Error checking cache database: {e}")
            return None

    def _save_to_cache_db(self, entity_id: int, entity_type: str, cached_url: str):
        """Save entity image cache entry to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            expiration = datetime.now() + timedelta(days=self.expiration_days)
            cursor.execute("""
                INSERT OR REPLACE INTO image_cache
                (entity_id, entity_type, cached_url, expiration_date)
                VALUES (?, ?, ?, ?)
            """, (entity_id, entity_type, cached_url, expiration))
            conn.commit()
            conn.close()
            logger.debug(f"Cached image for {entity_type}:{entity_id} -> {cached_url}")
        except Exception as e:
            logger.error(f"Error saving to cache database: {e}")

    @abstractmethod
    async def upload_image(self, local_file_path: str, entity_id: int, entity_type: str) -> Optional[str]:
        """Upload image to hosting backend and return a public URL, or None on failure."""
        ...

    def cleanup_expired(self):
        """Remove expired entries from cache database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM image_cache
                WHERE expiration_date <= ?
            """, (datetime.now(),))
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired cache entries")
        except Exception as e:
            logger.error(f"Error cleaning up expired cache entries: {e}")

    def get_cache_stats(self) -> dict:
        """Get cache statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM image_cache")
            total = cursor.fetchone()[0]
            cursor.execute("""
                SELECT COUNT(*) FROM image_cache
                WHERE expiration_date > ?
            """, (datetime.now(),))
            valid = cursor.fetchone()[0]
            conn.close()
            return {
                "total_entries": total,
                "valid_entries": valid,
                "expired_entries": total - valid,
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}


class ImageCacheTmpfiles(ImageCache):
    """Image cache backend using tmpfiles.org"""

    def __init__(
        self,
        db_path: str = "image_cache.db",
        expiration_days: int = 7,
    ):
        super().__init__(db_path=db_path, expiration_days=expiration_days)
        self.backend_url = "https://tmpfiles.org/api/v1/upload"
        self.payload_param = "file"

    def _convert_tmpfiles_url(self, url: str) -> str:
        """Convert tmpfiles.org page URL to a direct download link."""
        return url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/")

    async def upload_image(self, local_file_path: str, entity_id: int, entity_type: str) -> Optional[str]:
        cached_url = self._get_cached_url_from_db(entity_id, entity_type)
        if cached_url:
            logger.debug(f"Using cached image for {entity_type}:{entity_id}: {cached_url}")
            return cached_url

        file_path = Path(local_file_path)
        if not file_path.exists():
            logger.warning(f"Image file not found: {local_file_path}")
            return None

        close_after = self.client is None
        if close_after:
            self.client = httpx.AsyncClient(timeout=30.0)

        try:
            logger.debug(f"Uploading image to {self.backend_url}: {local_file_path}")
            with open(file_path, "rb") as f:
                assert self.client is not None
                response = await self.client.post(self.backend_url, files={self.payload_param: f})

            if response.status_code == 200:
                try:
                    response_data = response.json()
                    if response_data.get("status") == "success":
                        url = response_data.get("data", {}).get("url")
                        if url:
                            cached_url = self._convert_tmpfiles_url(url)
                            self._save_to_cache_db(entity_id, entity_type, cached_url)
                            logger.debug(f"Image uploaded successfully for {entity_type}:{entity_id}: {cached_url}")
                            return cached_url
                        else:
                            logger.warning(f"tmpfiles.org returned no URL in response: {response_data}")
                    else:
                        logger.warning(f"tmpfiles.org upload failed: {response_data.get('status', 'unknown')}")
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse tmpfiles.org response as JSON: {response.text}")
            else:
                logger.warning(f"tmpfiles.org upload failed with status {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
        finally:
            if close_after and self.client:
                await self.client.aclose()
                self.client = None

        return None


class ImageCacheImgBB(ImageCache):
    """Image cache backend using imgbb.com"""

    UPLOAD_URL = "https://api.imgbb.com/1/upload"

    def __init__(
        self,
        api_key: str,
        db_path: str = "image_cache.db",
        expiration_days: int = 7,
    ):
        super().__init__(db_path=db_path, expiration_days=expiration_days)
        self.api_key = api_key

    async def upload_image(self, local_file_path: str, entity_id: int, entity_type: str) -> Optional[str]:
        cached_url = self._get_cached_url_from_db(entity_id, entity_type)
        if cached_url:
            logger.debug(f"Using cached image for {entity_type}:{entity_id}: {cached_url}")
            return cached_url

        file_path = Path(local_file_path)
        if not file_path.exists():
            logger.warning(f"Image file not found: {local_file_path}")
            return None

        close_after = self.client is None
        if close_after:
            self.client = httpx.AsyncClient(timeout=30.0)

        try:
            logger.debug(f"Uploading image to ImgBB: {local_file_path}")
            with open(file_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            assert self.client is not None
            response = await self.client.post(
                self.UPLOAD_URL,
                params={"key": self.api_key},
                data={
                    "image": image_data,
                    "expiration": self.expiration_days * 86400,
                },
            )

            if response.status_code == 200:
                try:
                    response_data = response.json()
                    if response_data.get("success"):
                        url = response_data.get("data", {}).get("url")
                        if url:
                            self._save_to_cache_db(entity_id, entity_type, url)
                            logger.info(f"Image uploaded successfully for {entity_type}:{entity_id}: {url}")
                            return url
                        else:
                            logger.warning(f"ImgBB returned no URL in response: {response_data}")
                    else:
                        logger.warning(f"ImgBB upload failed: {response_data}")
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse ImgBB response as JSON: {response.text}")
            else:
                logger.warning(f"ImgBB upload failed with status {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error uploading image to ImgBB: {e}")
        finally:
            if close_after and self.client:
                await self.client.aclose()
                self.client = None

        return None

