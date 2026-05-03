# Telegram to Bark

A self-hosted Telegram userbot that forwards incoming messages as push notifications to [Bark](https://bark.day.app) (iOS).

## Features

- **Message forwarding** — forwards incoming text and media messages to your Bark endpoints
- **Activity suppression** — skips notifications while you are actively using Telegram (configurable idle timeout)
- **Archive filter** — ignores messages from archived chats, reloaded every hour
- **Mute filter** — respects Telegram's per-chat and global mute/silent notification settings
- **Image upload** — uploads favicon of the chat to an image hosting service and includes the URL in the notification so that Bark can display it as an icon; supports [tmpfiles.org](https://tmpfiles.org) (no key) and [ImgBB](https://imgbb.com) (free API key)


## Running

### Docker (recommended)

Place your `config.json` inside a `config/` folder next to `docker-compose.yml`, then:

```bash
docker compose up -d
```

The container auto-detects `/app/config/config.json` (mounted from `./config`).

Pull a pre-built image:

```bash
docker run -v ./config:/app/config ghcr.io/<your-username>/telegram_to_bark:latest
```

Force a clean rebuild:

```bash
docker compose build --no-cache
```

### Local

```bash
pip install -r requirements.txt
python telegram_bark_client.py
# or point to a config directory:
python telegram_bark_client.py --config-dir /path/to/config
```

Config is auto-detected in this order: current working directory → `/config` → `/app/config`.

## Setup

Run the interactive wizard to create `config.json` and generate a Telegram session string:

```bash
python init.py
# or for a specific directory:
python init.py --config-dir ./config
```

The wizard walks through all sections: Telegram credentials, Bark endpoints, activity timeout, logging, and image cache backend.

## Configuration

`config.json` (JSONC — `//` comments supported):

```jsonc
{
  "telegram": {
    "api_id": 12345678,             // from https://my.telegram.org/apps
    "api_hash": "your_api_hash",
    "phone_number": "+1234567890",
    "session_string": ""            // filled in by init.py
  },
  "bark": {
    "endpoints": [
      "https://api.day.app/YOUR_BARK_KEY"
    ],
    "group": null,                  // notification group name (optional)
    "sound": null                   // notification sound (optional)
  },
  "activity": {
    "timeout_seconds": 300          // suppress notifications for this many seconds after last activity
  },
  "logging": {
    "level": "INFO",
    "file": null                    // path to log file (optional)
  },
  "image_cache": {
    "backend": "tmpfiles",          // "tmpfiles" or "imgbb"
    "imgbb_api_key": null,          // required when backend is "imgbb"
    "expiration_days": 7,
    "db_path": "image_cache.db"
  }
}
```

### Image cache backends

| Backend | Key required | Notes |
|---|---|---|
| `tmpfiles` | No | Files expire after the configured days; URLs are direct download links |
| `imgbb` | Yes (free at [imgbb.com](https://api.imgbb.com)) | Persistent hosting with expiration support |

## Requirements

- Python 3.11+
- Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)
- [Bark](https://apps.apple.com/app/bark-customed-notifications/id1403753865) installed on your iPhone
