import asyncio
import logging
from datetime import datetime, timezone
from telethon.tl.types import InputNotifyChats, InputNotifyUsers, InputNotifyBroadcasts, InputNotifyForumTopic, PeerNotifySettings
from telethon.tl.types import Message, Dialog
from telethon.tl import functions
from telethon.client import TelegramClient
from util import get_sender_type, SenderType, get_sender_name
from telethon.tl.types import User, Chat, Channel

logger = logging.getLogger(__name__)


def check_is_muted(notify_settings: PeerNotifySettings, now: datetime) -> bool | None:
    if notify_settings.mute_until:
        if notify_settings.mute_until > now:
            return True
        else:
            return False
    elif notify_settings.silent:
        if notify_settings.silent is True:
            return True
        else:
            return False
    return None


class MessageFilter:
    def __init__(self, telegram_client: TelegramClient):
        self.telegram_client = telegram_client
        self.archived_chats: set[int] = set()
        self.notify_settings: dict[SenderType, PeerNotifySettings] = {}
        self._reload_task = None
        self._reload_interval = 60 * 60  # 1 hour in seconds

    async def initialize(self):
        """Initialize the filter by loading archived chats and starting the reload scheduler"""
        await self.load_archived_chats()
        await self.update_notify_settings()
        self._reload_task = asyncio.create_task(
            self._reload_archived_periodically())
        logger.info("MessageFilter initialized with archived chats loaded")

    async def cleanup(self):
        """Clean up resources, cancel the reload task"""
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
        logger.info("MessageFilter cleanup complete")

    async def update_notify_settings(self):
        try:
            self.notify_settings[SenderType.User] = await self.telegram_client(  # type: ignore
                functions.account.GetNotifySettingsRequest(
                    peer=InputNotifyUsers())
            )
            self.notify_settings[SenderType.Group] = await self.telegram_client(  # type: ignore
                functions.account.GetNotifySettingsRequest(
                    peer=InputNotifyChats())
            )
            self.notify_settings[SenderType.Channel] = await self.telegram_client(  # type: ignore
                functions.account.GetNotifySettingsRequest(
                    peer=InputNotifyBroadcasts())
            )
            for sender_type, settings in self.notify_settings.items():
                logger.info(
                    f"Loaded notification settings for {sender_type.value}: mute_until={settings.mute_until}, silent={settings.silent}")

        except Exception as e:
            logger.error(f"Error loading default notification settings: {e}")

    async def load_archived_chats(self):
        """Load all archived chat IDs into memory"""
        try:
            dialogs: dict[int, Dialog] = {
                d.id: d
                for d in await self.telegram_client.get_dialogs() if d.archived  # type: ignore
            }
            for d in dialogs.values():
                assert d.archived, f"Expected only archived dialogs, but got non-archived dialog with ID {d.id}"
                logging.info(f" - Loaded archived chat: {d.name} (ID: {d.id})")
            archived_ids = set(dialogs.keys())
            if archived_ids == self.archived_chats:
                return
            self.archived_chats = archived_ids

        except Exception as e:
            logger.error(f"Error loading archived chats: {e}")

    async def _reload_archived_periodically(self):
        """Reload archived chats every 30 minutes"""
        while True:
            try:
                await asyncio.sleep(self._reload_interval)
                await self.load_archived_chats()
                logger.info("Reloaded archived chats (scheduled)")
            except asyncio.CancelledError:
                logger.info("Archived chats reload task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic reload: {e}")
                # Continue running despite errors

    async def should_forward_message(self, message: Message, topic_id: int | None = None) -> bool:
        if not message or not message.peer_id:
            return False

        try:
            # Check if chat is archived
            chat_id: int = message.chat_id  # type: ignore
            if self._is_chat_archived(chat_id):
                logger.debug(f"Message from archived chat {chat_id}, skipping")
                return False

            # Check if chat (or topic) is muted (respects Telegram's notification settings)
            if await self._is_chat_muted(chat_id, topic_id=topic_id):
                return False

            logger.debug(f"Message from {chat_id} passed filters")
            return True
        except Exception as e:
            logger.error(f"Error checking message filters: {e}")
            return False

    def _is_chat_archived(self, chat_id: int) -> bool:
        """Check if the chat is archived using cached data"""
        try:
            # Check against in-memory set (O(1) lookup)
            is_archived = chat_id in self.archived_chats
            return is_archived
        except Exception as e:
            return False

    async def _is_chat_muted(self, chat_id: int, topic_id: int | None = None) -> bool:
        """Check if chat is muted (respects Telegram notification settings)"""
        try:
            # Get the chat entity (always the chat where message was sent)
            # For group messages, this ensures we check the GROUP's settings, not the sender's
            entity: User | Chat | Channel = await self.telegram_client.get_entity(chat_id) # type: ignore
            entity_name = get_sender_name(entity)
            sender_type = get_sender_type(entity)

            now = datetime.now(timezone.utc)

            # For forum topics, check the per-topic notification settings first
            if topic_id is not None and isinstance(entity, Channel):
                try:
                    input_peer = await self.telegram_client.get_input_entity(entity)
                    topic_notify_settings: PeerNotifySettings = await self.telegram_client(  # type: ignore
                        functions.account.GetNotifySettingsRequest(
                            peer=InputNotifyForumTopic(peer=input_peer, top_msg_id=topic_id))
                    )
                    is_muted = check_is_muted(topic_notify_settings, now)
                    if is_muted is not None:
                        if is_muted:
                            logger.debug(f"Message from muted topic {topic_id} in {entity_name} (ID: {chat_id}), skipping")
                        return is_muted
                except Exception as e:
                    logger.debug(f"Error checking topic mute status for topic {topic_id}: {e}")

            # Use GetNotifySettingsRequest to get notification settings for the specific chat
            notify_settings: PeerNotifySettings = await self.telegram_client(
                functions.account.GetNotifySettingsRequest(
                    peer=entity)  # type: ignore
            )

            if notify_settings:
                is_muted = check_is_muted(notify_settings, now)
                if is_muted is not None:
                    if is_muted:
                        logger.debug(f"Message from muted chat {entity_name} (ID: {chat_id}), skipping")
                    return is_muted

            notify_settings_for_type: PeerNotifySettings = self.notify_settings[sender_type]
            if notify_settings_for_type:
                is_muted = check_is_muted(notify_settings_for_type, now)
                if is_muted is not None:
                    if is_muted:
                        logger.debug(f"Message from chat {entity_name} (ID: {chat_id}) is muted based on default settings for {sender_type.value}, skipping")
                    return is_muted

            return False
        except Exception as e:
            logger.debug(f"Error checking mute status: {e}")
            return False

    async def mark_message_as_read(self, message: Message) -> bool:
        try:
            await self.telegram_client.send_read_acknowledge(
                message.chat_id, message=message
            )
            logger.debug(f"Marked message {message.id} as read")
            return True
        except Exception as e:
            logger.debug(f"Error marking message as read: {e}")
            return False
