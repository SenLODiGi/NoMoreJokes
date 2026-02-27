"""
NoMoreJokes Telegram Bot
Generates SEO-optimized articles via Gemini AI and saves them as HTML files.

Environment variables:
  TELEGRAM_BOT_TOKEN  – Telegram bot token from @BotFather (required)
  GEMINI_API_KEY      – Google Gemini API key (required)
  TELEGRAM_API_ID     – Telegram client API ID (optional, reserved for future use)
  TELEGRAM_API_HASH   – Telegram client API hash (optional, reserved for future use)
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Optional – reserved for future Telegram client usage
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "templates" / "template.html"
BLOG_DIR = BASE_DIR / "blog"

WAITING_FOR_TOPIC = 1  # ConversationHandler state

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def _build_prompt(topic: str) -> str:
    return "\n".join([
        "You are a skilled human journalist writing for a general audience.",
        "Write a compelling, emotionally engaging article about the following topic or headline.",
        "",
        f"Topic: {topic}",
        "",
        "Requirements:",
        "- Length: 800-1200 words",
        "- Style: human storytelling, emotional, engaging; avoid robotic or listicle tone",
        "- Structure: an introduction, at least three sections each with an H2 heading and",
        "  one or more H3 subheadings where appropriate, and a conclusion",
        "- Return a single JSON object with EXACTLY these keys:",
        '  "title": article title (string)',
        '  "slug": URL-safe slug, lowercase, hyphens only (string)',
        '  "meta_description": 150-160 characters summary for SEO (string)',
        '  "keywords": comma-separated SEO keywords, 5-8 keywords (string)',
        '  "html_content": the article body as clean HTML using <p>, <h2>, and <h3> tags only',
        "    (NO <html>, <head>, <body>, or <article> wrappers) (string)",
        "",
        "Return ONLY the JSON object with no markdown fences or extra text.",
    ])


def generate_article(topic: str) -> dict:
    """Call Gemini to generate article data and return a parsed dict."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(_build_prompt(topic))
    raw = response.text.strip()

    # Strip accidental markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)
    required = {"title", "slug", "meta_description", "keywords", "html_content"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Gemini response missing keys: {missing}")
    return data


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _sanitize_slug(slug: str) -> str:
    """Ensure the slug is filesystem-safe."""
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "article"


def render_html(data: dict) -> str:
    """Render the HTML template with article data."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc)
    replacements = {
        "{{ title }}": data["title"],
        "{{ slug }}": data["slug"],
        "{{ meta_description }}": data["meta_description"],
        "{{ keywords }}": data["keywords"],
        "{{ content }}": data["html_content"],
        "{{ date }}": now.strftime("%B %d, %Y"),
        "{{ year }}": str(now.year),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def save_article(data: dict) -> Path:
    """Save rendered HTML to blog/<slug>.html and return the path."""
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    slug = _sanitize_slug(data["slug"])
    output_path = BLOG_DIR / f"{slug}.html"
    output_path.write_text(render_html(data), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Telegram command handlers
# ---------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Welcome to *NoMoreJokes Bot*!\n\n"
        "Use /generate or /publish and then send me a news headline or trending topic "
        "to generate an SEO-optimized article.\n\n"
        "Use /cancel at any time to stop.",
        parse_mode="Markdown",
    )


async def generate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /generate and /publish – ask the user for a topic."""
    await update.message.reply_text(
        "📰 Send me a *news headline or trending topic* and I'll write a full article for you.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_TOPIC


async def topic_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the topic, call Gemini, generate HTML, and reply with the result."""
    topic = update.message.text.strip()
    if not topic:
        await update.message.reply_text("Please send a non-empty topic.")
        return WAITING_FOR_TOPIC

    await update.message.reply_text("⏳ Generating your article… this may take a moment.")

    try:
        data = generate_article(topic)
        path = save_article(data)
    except json.JSONDecodeError:
        logger.exception("Failed to parse Gemini response")
        await update.message.reply_text(
            "❌ Could not parse the AI response. Please try again later.",
        )
        return ConversationHandler.END
    except Exception:
        logger.exception("Article generation failed")
        await update.message.reply_text(
            "❌ An error occurred while generating the article. Please try again later.",
        )
        return ConversationHandler.END

    slug = _sanitize_slug(data["slug"])
    blog_url = f"https://senlodigi.github.io/NoMoreJokes/blog/{slug}.html"

    await update.message.reply_text(
        f"✅ *Article generated!*\n\n"
        f"📌 *Title:* {data['title']}\n"
        f"📝 *Meta:* {data['meta_description']}\n"
        f"🔑 *Keywords:* {data['keywords']}\n"
        f"📁 *Saved to:* `blog/{slug}.html`\n"
        f"🌐 *URL:* {blog_url}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❎ Cancelled. Use /generate or /publish to start again.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set. Exiting.")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("generate", generate_handler),
            CommandHandler("publish", generate_handler),
        ],
        states={
            WAITING_FOR_TOPIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topic_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(conv_handler)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
