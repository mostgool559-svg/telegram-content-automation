import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from telethon import TelegramClient

from filter_pro import is_suspicious_message


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

load_dotenv()

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "telegram_session")

SOURCE_CHANNEL_ID = int(os.environ["SOURCE_CHANNEL_ID"])
TARGET_CHANNEL_ID = int(os.environ["TARGET_CHANNEL_ID"])

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

TARGET_LINK = os.getenv("TARGET_LINK", "")

CHECK_INTERVAL_SECONDS = int(
    os.getenv("CHECK_INTERVAL_SECONDS", "3600")
)

MESSAGE_LIMIT = int(
    os.getenv("MESSAGE_LIMIT", "10")
)

ALLOWED_LINKS = [
    item.strip()
    for item in os.getenv("ALLOWED_LINKS", "").split(",")
    if item.strip()
]

REWRITE_STYLE = os.getenv(
    "REWRITE_STYLE",
    (
        "Rewrite the caption in a short, engaging social-media style. "
        "Keep it under 140 characters. Do not invent facts."
    ),
)

SENT_IDS_FILE = Path("data/sent_ids.json")
LOG_FILE = Path("logs/app.log")


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
SENT_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Clients
# ---------------------------------------------------------

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def clean_text(text: str) -> str:
    """Remove URLs, mentions and unnecessary whitespace."""
    if not text:
        return ""

    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def load_sent_ids() -> set[int]:
    """Load already processed Telegram message IDs."""
    if not SENT_IDS_FILE.exists():
        return set()

    try:
        with SENT_IDS_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return {int(item) for item in data}

    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.error("Could not load sent message IDs: %s", exc)
        return set()


def save_sent_ids(sent_ids: set[int]) -> None:
    """
    Save state using a temporary file first.
    This reduces the chance of corrupting the state file
    if the program stops during writing.
    """
    temp_file = SENT_IDS_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(
            sorted(sent_ids),
            file,
            ensure_ascii=False,
            indent=2,
        )

    temp_file.replace(SENT_IDS_FILE)


async def rewrite_caption(text: str) -> str:
    """Rewrite a caption using the OpenAI API."""
    if not text:
        return ""

    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": REWRITE_STYLE,
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            max_tokens=120,
            temperature=0.8,
        )

        rewritten = response.choices[0].message.content

        if rewritten:
            return rewritten.strip()

    except Exception:
        logger.exception("OpenAI caption rewrite failed.")

    # Safe fallback: keep the original text.
    return text


def build_caption(rewritten_text: str) -> str:
    """Build the final Telegram caption."""
    if TARGET_LINK:
        return f"{rewritten_text}\n\n💥 {TARGET_LINK}"

    return rewritten_text


# ---------------------------------------------------------
# Telegram processing
# ---------------------------------------------------------

async def find_source_channel(client: TelegramClient):
    dialogs = await client.get_dialogs(limit=100)

    for dialog in dialogs:
        entity = dialog.entity

        if getattr(entity, "id", None) == SOURCE_CHANNEL_ID:
            return entity

    return None


async def process_messages(
    client: TelegramClient,
    sent_ids: set[int],
) -> None:

    source_channel = await find_source_channel(client)

    if source_channel is None:
        logger.error(
            "Source channel %s was not found.",
            SOURCE_CHANNEL_ID,
        )
        return

    messages = await client.get_messages(
        source_channel,
        limit=MESSAGE_LIMIT,
    )

    logger.info("Fetched %d messages.", len(messages))

    # Oldest -> newest
    for message in reversed(messages):

        if message.id in sent_ids:
            continue

        original_text = message.message or ""

        # Skip advertising / suspicious content.
        if is_suspicious_message(
            original_text,
            ALLOWED_LINKS,
        ):
            logger.info(
                "Skipped message %s: filtered content.",
                message.id,
            )
            continue

        # This automation publishes videos only.
        if not message.video:
            logger.info(
                "Skipped message %s: no video.",
                message.id,
            )
            continue

        try:
            await process_video_message(
                client=client,
                message=message,
            )

        except Exception:
            logger.exception(
                "Failed to process message %s.",
                message.id,
            )
            continue

        # Mark as processed only after successful publishing.
        sent_ids.add(message.id)
        save_sent_ids(sent_ids)

        logger.info(
            "Message %s published successfully.",
            message.id,
        )


async def process_video_message(
    client: TelegramClient,
    message,
) -> None:

    original_text = message.message or ""

    cleaned_text = clean_text(original_text)

    if not cleaned_text:
        cleaned_text = "Watch until the end."

    rewritten_text = await rewrite_caption(cleaned_text)
    final_caption = build_caption(rewritten_text)

    # TemporaryDirectory automatically removes downloaded files.
    with tempfile.TemporaryDirectory() as temp_dir:

        video_path = Path(temp_dir) / f"video_{message.id}.mp4"

        await message.download_media(
            file=str(video_path)
        )

        logger.info(
            "Downloaded video for message %s.",
            message.id,
        )

        await client.send_file(
            TARGET_CHANNEL_ID,
            str(video_path),
            caption=final_caption,
            link_preview=False,
        )


# ---------------------------------------------------------
# Application
# ---------------------------------------------------------

async def run_cycle(
    client: TelegramClient,
    sent_ids: set[int],
) -> None:

    try:
        await process_messages(
            client=client,
            sent_ids=sent_ids,
        )

    except Exception:
        logger.exception("Processing cycle failed.")
def is_suspicious_message(text: str, allowed_links: list[str]) -> bool:
    """
    Detect messages that contain external links or Telegram mentions.

    Links explicitly listed in allowed_links are ignored.
    """
    if not text:
        return False

    normalized_text = text.lower()

    # Remove allowed links/mentions before checking.
    for allowed in allowed_links:
        normalized_text = normalized_text.replace(allowed.lower(), "")

    # Detect remaining URLs.
    url_patterns = [
        r"https?://\S+",
        r"t\.me/\S+",
        r"www\.\S+",
    ]

    for pattern in url_patterns:
        if re.search(pattern, normalized_text):
            return True

    # Detect foreign Telegram mentions.
    if re.search(r"@\w+", normalized_text):
        return True

    # Common advertising language.
    ad_keywords = [
        "реклама",
        "рекламный",
        "спонсор",
        "промокод",
        "скидка",
        "подписывай",
        "переходи",
        "заходи",
        "купи",
        "купить",
        "sale",
        "discount",
        "promo",
        "sponsor",
    ]

    return any(
        keyword in normalized_text
        for keyword in ad_keywords
    )

async def main() -> None:

    sent_ids = load_sent_ids()

    client = TelegramClient(
        TELEGRAM_SESSION,
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH,
    )

    await client.start()

    logger.info("Telegram automation started.")

    try:
        while True:
            await run_cycle(client, sent_ids)

            logger.info(
                "Next check in %s seconds.",
                CHECK_INTERVAL_SECONDS,
            )

            await asyncio.sleep(
                CHECK_INTERVAL_SECONDS
            )

    finally:
        await client.disconnect()
        logger.info("Telegram client disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Application stopped by user.")