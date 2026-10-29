import os
import re
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

# -----------------------------
# DEFAULT SETTINGS
# -----------------------------

settings = {
    "location": "Romford",
    "distance": 20,
    "keyword": "Warehouse",
    "part_time": True,
    "paused": False,
    "check_minutes": 5,
}

seen_jobs = set()
subscribers = set()

AMAZON_SEARCH = (
    "https://www.amazon.jobs/en-gb/search"
    "?base_query={keyword}"
    "&country=GBR"
    "&loc_query={location}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130 Safari/537.36"
    )
}


# -----------------------------
# AMAZON JOB SEARCH
# -----------------------------

def search_amazon_jobs():
    keyword = quote_plus(settings["keyword"])
    location = quote_plus(settings["location"])

    url = AMAZON_SEARCH.format(
        keyword=keyword,
        location=location,
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []

    # Amazon job cards
    cards = soup.select(
        "div.job-tile, "
        "div.job-card, "
        "article.job-card, "
        "div[class*='job-card'], "
        "div[class*='job-tile']"
    )

    for card in cards:
        link = card.find("a", href=True)

        if not link:
            continue

        href = link.get("href", "")

        if "/jobs/" not in href:
            continue

        if href.startswith("/"):
            href = "https://www.amazon.jobs" + href

        text = card.get_text(" ", strip=True)

        title = link.get_text(" ", strip=True)

        if not title:
            heading = card.find(["h2", "h3", "h4"])
            if heading:
                title = heading.get_text(" ", strip=True)

        job_id_match = re.search(
            r"(?:Job ID|jobId)[\s:]*([A-Z0-9-]+)",
            text,
            re.IGNORECASE,
        )

        job_id = (
            job_id_match.group(1)
            if job_id_match
            else href.rstrip("/").split("/")[-1]
        )

        # Part-time filtering
        if settings["part_time"]:
            lower_text = text.lower()

            part_time_words = [
                "part time",
                "part-time",
                "reduced time",
            ]

            if not any(word in lower_text for word in part_time_words):
                continue

        jobs.append(
            {
                "id": job_id,
                "title": title,
                "text": text,
                "url": href,
            }
        )

    return jobs


# -----------------------------
# TELEGRAM COMMANDS
# -----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)

    await update.message.reply_text(
        "🤖 Amazon UK Job Alert Bot is active!\n\n"
        "I will monitor Amazon warehouse jobs and alert you "
        "when a new matching listing appears.\n\n"
        "Commands:\n"
        "/status\n"
        "/location Romford\n"
        "/distance 20\n"
        "/job Warehouse\n"
        "/parttime on\n"
        "/check\n"
        "/pause\n"
        "/resume"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ CURRENT SETTINGS\n\n"
        f"📍 Location: {settings['location']}\n"
        f"📏 Distance: {settings['distance']} miles\n"
        f"🔎 Keyword: {settings['keyword']}\n"
        f"🕐 Part-time only: "
        f"{'ON' if settings['part_time'] else 'OFF'}\n"
        f"⏸ Paused: "
        f"{'YES' if settings['paused'] else 'NO'}\n"
        f"⏱ Check interval: {settings['check_minutes']} minutes"
    )


async def set_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/location Romford"
        )
        return

    settings["location"] = " ".join(context.args)

    await update.message.reply_text(
        f"📍 Location changed to: {settings['location']}"
    )


async def set_distance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/distance 20"
        )
        return

    try:
        distance = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Please enter a number.\nExample: /distance 20"
        )
        return

    settings["distance"] = distance

    await update.message.reply_text(
        f"📏 Search radius set to {distance} miles."
    )


async def set_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/job Warehouse"
        )
        return

    settings["keyword"] = " ".join(context.args)

    await update.message.reply_text(
        f"🔎 Job keyword changed to: {settings['keyword']}"
    )


async def set_parttime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/parttime on"
        )
        return

    value = context.args[0].lower()

    if value == "on":
        settings["part_time"] = True
        await update.message.reply_text(
            "🕐 Part-time filter: ON"
        )

    elif value == "off":
        settings["part_time"] = False
        await update.message.reply_text(
            "🕐 Part-time filter: OFF"
        )

    else:
        await update.message.reply_text(
            "Use:\n/parttime on\nor\n/parttime off"
        )


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings["paused"] = True

    await update.message.reply_text(
        "⏸ Job alerts paused."
    )


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings["paused"] = False

    await update.message.reply_text(
        "▶️ Job alerts resumed."
    )


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 Checking Amazon Jobs now..."
    )

    try:
        jobs = search_amazon_jobs()

        if not jobs:
            await update.message.reply_text(
                "No matching jobs found right now."
            )
            return

        message = f"🔎 Found {len(jobs)} matching job(s):\n\n"

        for job in jobs[:10]:
            message += (
                f"📌 {job['title']}\n"
                f"🆔 {job['id']}\n"
                f"🔗 {job['url']}\n\n"
            )

        await update.message.reply_text(message)

    except Exception as error:
        await update.message.reply_text(
            f"⚠️ Search error:\n{error}"
        )


# -----------------------------
# AUTOMATIC MONITOR
# -----------------------------

async def monitor_jobs(application):
    while True:

        try:
            if not settings["paused"]:

                jobs = search_amazon_jobs()

                for job in jobs:

                    job_id = job["id"]

                    if job_id in seen_jobs:
                        continue

                    seen_jobs.add(job_id)

                    message = (
                        "🚨 NEW AMAZON JOB FOUND\n\n"
                        f"📌 {job['title']}\n"
                        f"📍 {settings['location']}\n"
                        f"🆔 {job['id']}\n\n"
                        f"🔗 APPLY NOW:\n{job['url']}"
                    )

                    for chat_id in list(subscribers):

                        try:
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=message,
                            )
                        except Exception:
                            pass

        except Exception as error:
            print("Monitor error:", error)

        await asyncio_sleep(
            settings["check_minutes"] * 60
        )


async def asyncio_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)


# -----------------------------
# HEALTH CHECK FOR RENDER
# -----------------------------

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(
            b"Amazon Telegram Bot is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.environ.get("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# -----------------------------
# MAIN
# -----------------------------

def main():

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    application.add_handler(
        CommandHandler("location", set_location)
    )

    application.add_handler(
        CommandHandler("distance", set_distance)
    )

    application.add_handler(
        CommandHandler("job", set_job)
    )

    application.add_handler(
        CommandHandler("parttime", set_parttime)
    )

    application.add_handler(
        CommandHandler("pause", pause)
    )

    application.add_handler(
        CommandHandler("resume", resume)
    )

    application.add_handler(
        CommandHandler("check", check)
    )

    async def post_init(app):
        asyncio_task = app.create_task(
            monitor_jobs(app)
        )

    application.post_init = post_init

    print("Amazon Telegram Bot starting...")

    application.run_polling()


if __name__ == "__main__":
    main()
