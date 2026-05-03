import argparse
import asyncio
import logging
import os
import sys
from telethon import TelegramClient, events
from telethon import functions, types
from telethon.sessions import StringSession
from telethon.tl.types import Message, User
from telethon.tl.types import UserStatusOnline, UserStatusOffline, UpdateUserStatus, UpdateNotifySettings
from telethon.tl.types import UpdateFolderPeers

from config import Config
from notification_handler import NotificationHandler
from activity_tracker import ActivityTracker
from message_filter import MessageFilter, get_sender_type
from image_cache import ImageCache, ImageCacheTmpfiles, ImageCacheImgBB


def setup_logging(config: Config) -> None:
    """Configure logging based on config settings"""
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)

    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    if config.logging.file:
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[
                logging.FileHandler(config.logging.file),
                logging.StreamHandler(),
            ]
        )
    else:
        logging.basicConfig(
            level=log_level,
            format=log_format,
        )


logger = logging.getLogger(__name__)


class TelegramBarkClient:
    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.notification_handler = None
        self.image_cache = None
        self.activity_tracker = ActivityTracker(config.activity.timeout_seconds)
        self.message_filter = None

    async def initialize(self) -> None:
        logger.info("Initializing Telegram client...")

        if not self.config.telegram.session_string:
            logger.error(
                "No session string found. Please run 'python init.py' first."
            )
            sys.exit(1)

        self.client = TelegramClient(
            StringSession(self.config.telegram.session_string),
            self.config.telegram.api_id,
            self.config.telegram.api_hash,
        )

        await self.client.connect()

        if not await self.client.is_user_authorized():
            logger.error("Session expired or invalid. Please run 'python init.py' again.")
            sys.exit(1)

        me = await self.client.get_me()
        logger.info(f"Authenticated as {me.first_name} (@{me.username or 'N/A'})")
        active_sessions = (await self.client(functions.account.GetAuthorizationsRequest())).authorizations
        logger.info(f"Active sessions: {len(active_sessions)}")
        for session in active_sessions:
            device = session.device_model or "Unknown Device"
            app_name = session.app_name or "Unknown App"
            app_version = session.app_version or "Unknown Version"
            last_active = session.date_active.strftime("%Y-%m-%d %H:%M")
            hash_id = session.hash
            logger.info(f"  - {device} ({app_name} {app_version}), last active: {last_active}: {hash_id}")



        # Initialize image cache
        ic = self.config.image_cache
        if ic.backend == "imgbb":
            if not ic.imgbb_api_key:
                logger.error("image_cache.imgbb_api_key is required when backend is 'imgbb'")
                sys.exit(1)
            self.image_cache = ImageCacheImgBB(
                api_key=ic.imgbb_api_key,
                db_path=ic.db_path,
                expiration_days=ic.expiration_days,
            )
        else:
            self.image_cache = ImageCacheTmpfiles(
                db_path=ic.db_path,
                expiration_days=ic.expiration_days,
            )
        logger.info(f"Initialized image cache with backend '{ic.backend}' and db path '{ic.db_path}'")
        self.notification_handler = NotificationHandler(
            self.config.bark.endpoints,
            group=self.config.bark.group,
            sound=self.config.bark.sound,
            image_cache=self.image_cache,
        )
        self.message_filter = MessageFilter(self.client)

        # Initialize message filter (loads archived chats and starts scheduler)
        await self.message_filter.initialize()

        self.client.add_event_handler(self.on_new_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self.on_user_status, events.Raw(UpdateUserStatus))
        self.client.add_event_handler(self.on_message_read, events.MessageRead(inbox=True))
        self.client.add_event_handler(self.on_update_notify_settings, events.Raw(UpdateNotifySettings))
        self.client.add_event_handler(self.on_folder_peers, events.Raw(UpdateFolderPeers))
        logger.info("Client initialized successfully")

    async def on_user_status(self, update) -> None:
        try:
            me = await self.client.get_me()
            if update.user_id == me.id:
                if isinstance(update.status, UserStatusOnline):
                    self.activity_tracker.record_activity()
                    logger.info("User came online")
                elif isinstance(update.status, UserStatusOffline):
                    logger.info("User went offline")
        except Exception as e:
            logger.error(f"Error handling user status event: {e}")

    async def on_message_read(self, event) -> None:
        try:
            self.activity_tracker.record_activity()
            logger.info(f"User read messages in chat {event.chat_id}")
        except Exception as e:
            logger.error(f"Error handling message read event: {e}")

    async def on_new_message(self, event: events.newmessage.NewMessage.Event) -> None:
        try:
            message: Message = event.message

            if message.out:
                return

            if not message.text and not message.media:
                return

            if self.activity_tracker.is_user_active():
                logger.debug(
                    f"Skipping notification - user is actively using Telegram "
                    f"(time until idle: {self.activity_tracker.get_time_until_idle():.1f}s)"
                )
                return

            sender = await event.get_sender()
            if not await self.message_filter.should_forward_message(message):
                return

            message_text = message.text or "[Media]"

            async with self.notification_handler as handler:
                await handler.send_notification(
                    self.client,
                    sender,
                    message_text,
                    message.chat_id,
                    message.id,
                )

        except Exception as e:
            logger.error(f"Error handling new message: {e}")

    async def on_update_notify_settings(self, update: UpdateNotifySettings) -> None:
        try:
            if isinstance(update.peer, (types.NotifyUsers, types.NotifyChats, types.NotifyBroadcasts)):
                await self.message_filter.update_notify_settings()
        except Exception as e:
            logger.error(f"Error handling notification settings update: {e}")

    async def on_folder_peers(self, update) -> None:
        try:
            logger.info("Chat archive status updated, reloading archived chats...")
            await self.message_filter.load_archived_chats()
        except Exception as e:
            logger.error(f"Error handling archive update: {e}")

    async def run(self) -> None:
        try:
            logger.info("Starting Telegram to Bark client...")
            logger.info(f"Forwarding notifications to: {', '.join(self.config.bark.endpoints)}")
            logger.info(f"Activity timeout: {self.config.activity.timeout_seconds}s")
            logger.info("Listening for new messages... Press Ctrl+C to stop")

            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            raise
        finally:
            if self.message_filter:
                await self.message_filter.cleanup()
            if self.image_cache:
                self.image_cache.cleanup_expired()
            if self.client:
                await self.client.disconnect()

    async def start(self) -> None:
        await self.initialize()
        await self.run()


def find_config_dir(cli_dir: str | None) -> str:
    """Return the directory containing config.json."""
    if cli_dir is not None:
        return cli_dir
    candidates = [
        os.getcwd(),
        "/config",
        "/app/config",
    ]
    for d in candidates:
        if os.path.isfile(os.path.join(d, "config.json")):
            return d
    raise FileNotFoundError(
        "config.json not found in any of: " + ", ".join(candidates) + "\n"
        "Please specify a config directory with --config-dir or run 'python init.py'."
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram to Bark notification bridge")
    parser.add_argument(
        "--config-dir",
        default=None,
        metavar="DIR",
        help="Directory containing config.json (default: auto-detect)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    startup_logger = logging.getLogger(__name__)

    try:
        config_dir = find_config_dir(args.config_dir)
    except FileNotFoundError as e:
        startup_logger.error(str(e))
        sys.exit(1)

    config_path = os.path.join(config_dir, "config.json")
    startup_logger.info(f"Using config directory: {config_dir}")

    # Resolve db_path relative to config_dir if it is not absolute
    try:
        config = Config.load(config_path)
    except FileNotFoundError as e:
        startup_logger.error(str(e))
        sys.exit(1)

    if not os.path.isabs(config.image_cache.db_path):
        config.image_cache.db_path = os.path.join(config_dir, config.image_cache.db_path)

    setup_logging(config)
    client = TelegramBarkClient(config)
    await client.start()

def main_sync() -> None:
    asyncio.run(main())

if __name__ == "__main__":
    main_sync()


