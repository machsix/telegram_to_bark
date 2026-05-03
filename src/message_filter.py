import asyncio
import logging
from datetime import datetime, timezone
from telethon.tl.types import InputNotifyChats, InputNotifyUsers, InputNotifyBroadcasts, PeerNotifySettings
from telethon.tl.types import Message, User, Chat, Channel, Dialog
from telethon.tl.types import NotifyChats, NotifyUsers, NotifyBroadcasts, NotifyPeer
from telethon.tl import functions
from telethon.client import TelegramClient
import enum

class SenderType(enum.Enum):
    User = "User"
    Group = "Group"
    Channel = "Channel"

def get_sender_type(entity) -> SenderType:
    if isinstance(entity, (User, NotifyPeer)):
        return SenderType.User
    elif isinstance(entity, (Chat, InputNotifyChats,  InputNotifyUsers,  NotifyUsers, NotifyChats)):
        return SenderType.Group
    elif isinstance(entity, (Channel, InputNotifyBroadcasts, NotifyBroadcasts)):
        return SenderType.Channel
    else:
        raise ValueError(f"Unknown entity type: {type(entity)}")


logger = logging.getLogger(__name__)


class MessageFilter:
    def __init__(self, telegram_client: TelegramClient):
        self.telegram_client = telegram_client
        self.archived_chats: set[int] = set()
        self.notify_settings: dict[SenderType, PeerNotifySettings] = {}
        self._reload_task = None
        self._reload_interval = 60 * 60 # 1 hour in seconds

    async def initialize(self):
        """Initialize the filter by loading archived chats and starting the reload scheduler"""
        await self.load_archived_chats()
        await self.update_notify_settings()
        self._reload_task = asyncio.create_task(self._reload_archived_periodically())
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
            self.notify_settings[SenderType.User] = await self.telegram_client( # type: ignore
                functions.account.GetNotifySettingsRequest(peer=InputNotifyUsers())
            )
            self.notify_settings[SenderType.Group] = await self.telegram_client( # type: ignore
                functions.account.GetNotifySettingsRequest(peer=InputNotifyChats())
            )
            self.notify_settings[SenderType.Channel] = await self.telegram_client( # type: ignore
                functions.account.GetNotifySettingsRequest(peer=InputNotifyBroadcasts())
            )
            logger.info("Loaded default notification settings for User, Group, and Channel")
        except Exception as e:
            logger.error(f"Error loading default notification settings: {e}")


    async def load_archived_chats(self):
        """Load all archived chat IDs into memory"""
        try:
            dialogs: dict[int, Dialog] = {
                d.id: d
                for d in await self.telegram_client.get_dialogs() if d.archived # type: ignore
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

    async def should_forward_message(self, message: Message) -> bool:
        if not message or not message.peer_id:
            return False

        try:
            # Check if chat is archived
            chat_id: int = message.chat_id # type: ignore
            if self._is_chat_archived(chat_id):
                logger.debug(f"Message from archived chat {chat_id}, skipping")
                return False

            # Check if chat is muted (respects Telegram's notification settings)
            if await self._is_chat_muted(chat_id):
                logger.debug(f"Message from muted chat {chat_id}, skipping")
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

    async def _is_chat_muted(self, chat_id: int) -> bool:
        """Check if chat is muted (respects Telegram notification settings)"""
        try:
            # Get the chat entity (always the chat where message was sent)
            # For group messages, this ensures we check the GROUP's settings, not the sender's
            entity = await self.telegram_client.get_entity(chat_id)
            sender_type = get_sender_type(entity)

            # Use GetNotifySettingsRequest to get notification settings for the specific chat
            notify_settings: PeerNotifySettings = await self.telegram_client(
                functions.account.GetNotifySettingsRequest(peer=entity) # type: ignore
            )

            if notify_settings:
                if notify_settings.mute_until:
                    # If mute_until is in the future or very large (permanent mute), chat is muted
                    if notify_settings.mute_until > datetime.now(timezone.utc):
                        logger.debug(f"Chat {chat_id} is muted until {notify_settings.mute_until}")
                        return True
                elif notify_settings.silent:
                    return True

            notify_settings_for_type: PeerNotifySettings = self.notify_settings[sender_type]
            if notify_settings_for_type.mute_until:
                if notify_settings_for_type.mute_until > datetime.now(timezone.utc):
                    logger.debug(f"Sender type {sender_type} is muted until {notify_settings_for_type.mute_until}")
                    return True
            elif notify_settings_for_type.silent:
                return True
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
