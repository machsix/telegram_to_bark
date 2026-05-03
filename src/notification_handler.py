import asyncio
import httpx
import logging
from pathlib import Path
from typing import Optional
from telethon.tl.types import User, Chat, Channel
from telethon.client import TelegramClient
from image_cache import ImageCache


logger = logging.getLogger(__name__)


class NotificationHandler:
    def __init__(
        self,
        bark_endpoints: list[str],
        group: Optional[str] = None,
        sound: Optional[str] = None,
        image_cache: Optional[ImageCache] = None,
    ):
        self.bark_endpoints = bark_endpoints
        self.group = group
        self.sound = sound
        self.image_cache = image_cache
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    async def send_notification(
        self,
        telegram_client: TelegramClient,
        sender: User | Chat | Channel,
        message_text: str,
        chat_id: int,
        message_id: int,
    ) -> bool:
        try:
            # Get the chat entity to determine if it's a group message
            chat = await telegram_client.get_entity(chat_id)

            message_preview = self._truncate_message(message_text, 200)

            # For messages in groups/channels, use group info; for direct messages, use sender info
            if isinstance(chat, (Chat, Channel)) and isinstance(sender, User):
                # User message in a group/channel
                sender_name = self._get_sender_name(sender)
                group_name = self._get_sender_name(chat)
                title = f"{sender_name} in {group_name}"
                icon = await self._get_sender_avatar(telegram_client, chat)
                deep_link = self._get_deep_link(chat, chat_id, message_id)
            else:
                # Direct message from user or other
                title = self._get_sender_name(sender)
                icon = await self._get_sender_avatar(telegram_client, sender)
                deep_link = self._get_deep_link(sender, chat_id, message_id)

            payload = {
                "title": title,
                "body": message_preview,
            }

            if self.group:
                payload["group"] = self.group
            if self.sound:
                payload["sound"] = self.sound
            if icon:
                payload["icon"] = icon
            if deep_link:
                payload["url"] = deep_link

            tasks = [
                self._send_to_endpoint(endpoint, payload)
                for endpoint in self.bark_endpoints
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            success_count = sum(1 for r in results if r is True)
            if success_count > 0:
                logger.info(
                    f"Notification sent for message from {title} "
                    f"({success_count}/{len(self.bark_endpoints)} endpoints): {payload}"
                )
                return True
            else:
                logger.warning(
                    f"Failed to send notification to any Bark endpoint for {title}"
                )
                return False
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            return False

    async def _send_to_endpoint(self, endpoint: str, payload: dict) -> bool:
        try:
            if not self.client:
                return False

            response = await self.client.post(endpoint, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Failed to send to {endpoint}: {e}")
            return False

    @staticmethod
    def _get_sender_name(sender: User | Chat | Channel) -> str:
        if isinstance(sender, User):
            if sender.first_name:
                if sender.last_name:
                    return f"{sender.first_name} {sender.last_name}"
                return sender.first_name
            return sender.username or "Unknown User"
        elif isinstance(sender, Chat):
            return sender.title or "Group"
        elif isinstance(sender, Channel):
            return sender.title or "Channel"
        return "Unknown"

    async def _get_sender_avatar(self, telegram_client: TelegramClient, sender: User | Chat | Channel) -> str:
        try:
            if not hasattr(sender, 'photo') or not sender.photo:
                logger.debug(f"No profile photo for {self._get_sender_name(sender)}")
                return ""

            # If no image cache, return empty string
            if not self.image_cache:
                logger.debug("Image cache not configured")
                return ""

            # Determine entity type and ID
            entity_id = sender.id
            if isinstance(sender, User):
                entity_type = "user"
            elif isinstance(sender, Chat):
                entity_type = "chat"
            elif isinstance(sender, Channel):
                entity_type = "channel"
            else:
                entity_type = "unknown"

            # Check cache first - avoid downloading if already cached
            cached_url = self.image_cache._get_cached_url_from_db(entity_id, entity_type)
            if cached_url:
                logger.debug(f"Using cached avatar for {entity_type}:{entity_id} ({self._get_sender_name(sender)})")
                return cached_url

            try:
                # Download profile photo only if not in cache
                import tempfile
                import os

                # Create a temporary file with .jpg extension
                fd, tmp_path = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)  # Close the file descriptor, Telethon will write to the path

                # Download profile photo to the temporary file
                result = await telegram_client.download_profile_photo(sender, file=tmp_path)

                if not result:
                    logger.debug(f"Failed to download profile photo for {self._get_sender_name(sender)}")
                    # Clean up the empty temp file
                    try:
                        Path(tmp_path).unlink()
                    except Exception:
                        pass
                    return ""

                logger.debug(f"Downloaded profile photo to {tmp_path}")

                try:
                    # Upload to cache backend and get cached URL (with entity info)
                    cached_url = await self.image_cache.upload_image(tmp_path, entity_id, entity_type)
                    if cached_url:
                        logger.debug(f"Profile photo cached at {cached_url}")
                    else:
                        logger.warning(f"Failed to cache profile photo for {self._get_sender_name(sender)}")
                    return cached_url if cached_url else ""
                finally:
                    # Delete the downloaded profile photo file, even if upload fails
                    try:
                        Path(tmp_path).unlink()
                        logger.debug(f"Deleted temporary file {tmp_path}")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Error downloading profile photo for {self._get_sender_name(sender)}: {e}")
                return ""

        except Exception as e:
            logger.error(f"Error getting sender avatar: {e}")
            return ""

    @staticmethod
    def _get_deep_link(sender: User | Chat | Channel, chat_id: int, message_id: int) -> str:
        if isinstance(sender, User):
            # Direct message: tg://resolve?domain=<username>
            username = sender.username
            if username:
                return f"tg://resolve?domain={username}"
            # For users without username, use user_id
            return f"tg://openmessage?user_id={chat_id}"
        elif isinstance(sender, (Chat, Channel)):
            # Group/Channel message
            username = getattr(sender, 'username', None)
            if username:
                # Public group/channel with username
                return f"tg://resolve?domain={username}&post={message_id}"
            else:
                # Private group/channel without username - use channel_id format
                # Note: For groups, use the actual chat_id; for supergroups/channels, need to convert
                if isinstance(sender, Channel):
                    # Supergroup or Channel - convert ID for deep link
                    # Telegram uses channel ID format: remove the -100 prefix if present
                    channel_id = abs(chat_id)
                    if chat_id < 0:
                        # Remove -100 prefix for supergroups
                        channel_id_str = str(abs(chat_id))
                        if channel_id_str.startswith('100'):
                            channel_id = int(channel_id_str[3:])
                    return f"tg://privatepost?channel={channel_id}&post={message_id}"
                else:
                    # Regular group chat
                    return f"tg://openmessage?chat_id={abs(chat_id)}"
        return "tg://home"

    @staticmethod
    def _truncate_message(text: str, max_length: int = 200) -> str:
        if len(text) > max_length:
            return text[:max_length].rstrip() + "..."
        return text
