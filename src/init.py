import argparse
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

from config import (
    Config,
    TelegramConfig,
    BarkConfig,
    ActivityConfig,
    LoggingConfig,
    ImageCacheConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def prompt(label: str, default: str | None = None, password: bool = False) -> str:
    """Prompt user for input, showing current/default value."""
    suffix = f" [{default}]" if default is not None else ""
    if password:
        import getpass
        value = getpass.getpass(f"{label}{suffix}: ").strip()
    else:
        value = input(f"{label}{suffix}: ").strip()
    return value if value else (default or "")


def prompt_int(label: str, default: int | None = None) -> int:
    while True:
        raw = prompt(label, str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a valid integer.")


def prompt_optional(label: str, default: str | None = None) -> str | None:
    """Return None if user enters nothing and there is no default."""
    suffix = f" [{default or 'none'}]"
    value = input(f"{label}{suffix}: ").strip()
    if not value:
        return default or None
    return value


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Section wizards
# ---------------------------------------------------------------------------

async def configure_telegram(existing: TelegramConfig | None) -> TelegramConfig:
    section("Telegram credentials  (https://my.telegram.org/apps)")

    api_id = prompt_int("API ID", existing.api_id if existing else None)
    api_hash = prompt("API Hash", existing.api_hash if existing else None)
    phone = prompt("Phone number (e.g. +1234567890)", existing.phone_number if existing else None)

    # Decide whether to reuse the session
    if existing and existing.session_string:
        keep = prompt("Keep existing session string? (y/n)", "y").lower()
        if keep == "y":
            print("  ✓ Reusing existing session")
            return TelegramConfig(
                api_id=api_id,
                api_hash=api_hash,
                phone_number=phone,
                session_string=existing.session_string,
            )

    # Generate a new session
    print("\n  Connecting to Telegram to generate a new session…")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("  Sending verification code…")
        await client.send_code_request(phone)
        code = prompt("Verification code")
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = prompt("2FA password", password=True)
            await client.sign_in(password=password)

    me = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()

    print(f"  ✓ Authenticated as {me.first_name} (@{me.username or 'N/A'})")
    return TelegramConfig(
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone,
        session_string=session_string,
    )


def configure_bark(existing: BarkConfig | None) -> BarkConfig:
    section("Bark endpoints")

    if existing:
        print("  Current endpoints:")
        for ep in existing.endpoints:
            print(f"    • {ep}")
        keep = prompt("Keep existing endpoints? (y/n)", "y").lower()
        if keep == "y":
            endpoints = existing.endpoints
        else:
            endpoints = _collect_endpoints()
    else:
        endpoints = _collect_endpoints()

    group = prompt_optional("Notification group (optional)", existing.group if existing else None)
    sound = prompt_optional("Notification sound (optional)", existing.sound if existing else None)
    return BarkConfig(endpoints=endpoints, group=group, sound=sound)


def _collect_endpoints() -> list[str]:
    endpoints: list[str] = []
    print("  Enter Bark endpoints one per line, empty line to finish:")
    while True:
        ep = input(f"  Endpoint {len(endpoints) + 1}: ").strip()
        if not ep:
            if endpoints:
                break
            print("  At least one endpoint is required.")
            continue
        if not ep.startswith("http"):
            ep = f"https://{ep}"
        endpoints.append(ep)
    return endpoints


def configure_activity(existing: ActivityConfig | None) -> ActivityConfig:
    section("Activity settings")
    timeout = prompt_int(
        "Idle timeout in seconds (notifications suppressed while active)",
        existing.timeout_seconds if existing else 300,
    )
    return ActivityConfig(
        timeout_seconds=timeout,
        session_hash=existing.session_hash if existing else None,
    )


def configure_logging(existing: LoggingConfig | None) -> LoggingConfig:
    section("Logging")
    level = prompt("Log level (DEBUG/INFO/WARNING/ERROR)", existing.level if existing else "INFO").upper()
    log_file = prompt_optional("Log file path (optional)", existing.file if existing else None)
    return LoggingConfig(level=level, file=log_file)


def configure_image_cache(existing: ImageCacheConfig | None) -> ImageCacheConfig:
    section("Image cache")
    print("  Backends: tmpfiles  (no key needed) | imgbb  (free API key at imgbb.com)")
    backend = prompt("Backend", existing.backend if existing else "tmpfiles").lower()
    imgbb_api_key: str | None = None
    if backend == "imgbb":
        imgbb_api_key = prompt_optional("ImgBB API key", existing.imgbb_api_key if existing else None)
    expiration_days = prompt_int("Cache expiration in days", existing.expiration_days if existing else 7)
    db_path = prompt("SQLite DB path", existing.db_path if existing else "image_cache.db")
    return ImageCacheConfig(
        backend=backend,
        imgbb_api_key=imgbb_api_key,
        expiration_days=expiration_days,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(config_path: str) -> None:
    print("\n" + "=" * 60)
    print("  Telegram → Bark  —  configuration wizard")
    print("=" * 60)
    print(f"  Config file: {config_path}")

    existing: Config | None = None
    if os.path.exists(config_path):
        try:
            existing = Config.load(config_path)
            print("  Existing configuration loaded — press Enter to keep current values.")
        except Exception as e:
            print(f"  Warning: could not load existing config ({e}). Starting fresh.")

    telegram = await configure_telegram(existing.telegram if existing else None)
    bark = configure_bark(existing.bark if existing else None)
    activity = configure_activity(existing.activity if existing else None)
    logging_cfg = configure_logging(existing.logging if existing else None)
    image_cache = configure_image_cache(existing.image_cache if existing else None)

    config = Config(
        telegram=telegram,
        bark=bark,
        activity=activity,
        logging=logging_cfg,
        image_cache=image_cache,
    )
    config.save(config_path)

    print(f"\n  ✓ Configuration saved to {config_path}")
    print("  Start the client with:")
    print("    python telegram_bark_client.py\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram to Bark — setup wizard")
    parser.add_argument(
        "--config-dir",
        default=None,
        metavar="DIR",
        help="Directory to write config.json into (default: current directory)",
    )
    args = parser.parse_args()

    config_dir = args.config_dir or os.getcwd()
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")

    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()

