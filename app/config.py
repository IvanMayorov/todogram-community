"""Settings from environment (.env). Secrets live only there."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    bot_token: str = _get("BOT_TOKEN")

    # AI (any OpenAI-compatible API: Гидра, OpenRouter, Hermes, direct)
    ai_base_url: str = _get("AI_BASE_URL")
    ai_api_key: str = _get("AI_API_KEY")
    ai_model: str = _get("AI_MODEL", "gemini-3.1-pro" if ai_base_url.rstrip("/") == "https://api.hydraai.ru/v1" else "gpt-4o-mini")

    # Speech: "deepgram" | "api" = Whisper via the AI provider | "local" = faster-whisper | "off"
    whisper_mode: str = _get("WHISPER_MODE", "api")
    deepgram_api_key: str = _get("DEEPGRAM_API_KEY")
    deepgram_model: str = _get("DEEPGRAM_MODEL", "nova-3")
    whisper_model: str = _get("WHISPER_MODEL", "whisper-1")
    whisper_base_url: str = _get("WHISPER_BASE_URL")  # empty = AI_BASE_URL
    whisper_api_key: str = _get("WHISPER_API_KEY")    # empty = AI_API_KEY
    whisper_local_model: str = _get("WHISPER_LOCAL_MODEL", "small")

    # Reactions and timing
    emoji_draft: str = _get("EMOJI_DRAFT", "✍")
    emoji_now: str = _get("EMOJI_NOW", "⚡")
    auto_create_seconds: int = int(_get("AUTO_CREATE_SECONDS", "60"))
    buffer_days: int = int(_get("BUFFER_DAYS", "14"))

    # Auto mode (/auto on): look for tasks after this much silence in the chat
    auto_pause_seconds: int = int(_get("AUTO_PAUSE_SECONDS", "120"))
    auto_max_batch: int = int(_get("AUTO_MAX_BATCH", "150"))  # check right away once this many piled up

    # Mini app
    web_host: str = _get("WEB_HOST", "0.0.0.0")
    web_port: int = int(_get("WEB_PORT", "8787"))
    public_url: str = _get("PUBLIC_URL")  # https address of the mini app (tunnel or domain)
    # Link the «Открыть» button uses. {bot} and {param} are filled in.
    # Main Mini App: https://t.me/{bot}?startapp={param}
    # App made with /newapp: https://t.me/{bot}/<short_name>?startapp={param}
    miniapp_link: str = _get("MINIAPP_LINK", "https://t.me/{bot}?startapp={param}")
    media_secret: str = _get("MEDIA_SECRET")  # empty = derived from BOT_TOKEN
    # Token for AI agents (MCP server): full access to every project. Empty = agent API off.
    agent_token: str = _get("AGENT_TOKEN")
    # the name people see in links (t.me/<name>): a second, collectible username of the same bot; empty = the bot's own
    bot_link_username: str = _get("BOT_LINK_USERNAME")
    # who hears about new people on the alpha's waiting list (a Telegram user id; empty = nobody)
    closed_alpha: bool = _get("CLOSED_ALPHA") == "1"
    admin_id: int = int(_get("ADMIN_ID", "0") or 0)

    db_path: str = _get("DB_PATH", str(ROOT / "data" / "bot.sqlite3"))
    cache_dir: str = _get("CACHE_DIR", str(ROOT / "data" / "cache"))
    log_level: str = _get("LOG_LEVEL", "INFO")


settings = Settings()


def norm_emoji(e: str) -> str:
    """Telegram sends ✍ without the variation selector; users may type ✍️."""
    return e.replace("️", "")
