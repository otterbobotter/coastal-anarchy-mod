"""
Coastal Anarchy Forum Moderation Bot
-------------------------------------
Monitors the forum RSS feed for new posts, scrapes the actual thread
content, runs a local profanity check, and sends flagged posts to
Groq AI for a full moderation report sent to Discord.

Setup:
  pip install feedparser requests beautifulsoup4

Credentials are loaded from environment variables (set these in Railway):
  DISCORD_WEBHOOK_URL
  GROQ_API_KEY
"""

import os
import feedparser
import requests
import time
from datetime import datetime
from bs4 import BeautifulSoup

# ============================================================
# CONFIG - credentials loaded from environment variables
# Set these in Railway's "Variables" tab, not here!
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GROQ_API_KEY        = os.environ.get("GROQ_API_KEY")
RSS_FEED_URL        = "https://coastal-anarchy.boards.net/rss/public"

# How often to check for new posts (in seconds)
CHECK_INTERVAL = 60

# ============================================================
# PROFANITY WORD LIST (first-pass filter, free, no API needed)
# Add or remove words as needed.
# ============================================================

PROFANITY_LIST = [
    "fuck", "shit", "bitch", "ass", "asshole", "damn", "crap",
    "bastard", "dick", "piss", "cunt", "faggot", "retard",
    # add more as needed
]

# ============================================================
# TRACKING - keeps track of posts we've already seen
# ============================================================

seen_post_ids = set()


def contains_profanity(text: str) -> bool:
    """Quick local check - no API call needed."""
    text_lower = text.lower()
    for word in PROFANITY_LIST:
        if word in text_lower:
            return True
    return False


def scrape_thread(url: str) -> list[dict]:
    """
    Fetches a thread page and returns a list of posts.
    Each post is a dict with 'author' and 'content' keys.
    """
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"  ❌ Could not fetch thread page: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    posts = []

    # ProBoards puts posts inside <td> cells that contain "Post by USERNAME"
    # We look for any td that contains a "Post by" marker
    for td in soup.find_all("td"):
        text = td.get_text(separator=" ", strip=True)

        # Skip tiny cells (nav, buttons, etc.)
        if len(text) < 20:
            continue

        # ProBoards post cells contain "Post by username on date"
        if "Post by" not in text:
            continue

        # Try to extract the author from the "Post by X on date" pattern
        author = "Unknown"
        try:
            after = text.split("Post by")[1]
            author = after.split("on")[0].strip()
        except Exception:
            pass

        # Remove the "Post by X on date" header from the content
        try:
            content = text.split("Back to Top")[1].strip() if "Back to Top" in text else text
        except Exception:
            content = text

        if content:
            posts.append({"author": author, "content": content})

    return posts


def ask_groq(post_text: str, author: str) -> str:
    """
    Sends the post to Groq and asks for a one-sentence bullying assessment.
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    prompt = (
        f"You are a school forum moderator. A student named '{author}' posted:\n\n"
        f"\"{post_text}\"\n\n"
        "In one sentence, describe whether this message contains cyberbullying, "
        "harassment, or targeted meanness toward another person. "
        "If it does not, say so plainly. Be concise and neutral."
    )

    body = {
        "model": "llama3-8b-8192",
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[Error contacting Groq: {e}]"


def send_discord_report(post_time: str, author: str,
                        profanity: bool, bullying_summary: str, post_url: str):
    """Sends a moderation report to the Discord webhook."""

    report = (
        f"🚨 **MODERATION FLAG**\n"
        f"```\n"
        f"message [{post_time}] by [{author}]\n"
        f"profanity: {profanity}\n"
        f"bullying: {bullying_summary}\n"
        f"```\n"
        f"🔗 {post_url}"
    )

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"content": report}, timeout=10)
        response.raise_for_status()
        print(f"  ✅ Report sent to Discord.")
    except Exception as e:
        print(f"  ❌ Failed to send Discord report: {e}")


def check_feed():
    """Fetches the RSS feed and processes any new threads."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking feed...")

    try:
        feed = feedparser.parse(RSS_FEED_URL)
    except Exception as e:
        print(f"  ❌ Could not fetch RSS feed: {e}")
        return

    for entry in feed.entries:
        post_id   = entry.get("id", entry.get("link", ""))
        link      = entry.get("link", "")
        published = entry.get("published", "Unknown time")
        title     = entry.get("title", "No title")

        if post_id in seen_post_ids:
            continue

        seen_post_ids.add(post_id)
        print(f"  📄 New activity in thread: '{title}'")

        posts = scrape_thread(link)

        if not posts:
            print(f"  ⚠️  Could not scrape any posts from thread.")
            continue

        print(f"  📝 Found {len(posts)} post(s) in thread.")

        for post in posts:
            author  = post["author"]
            content = post["content"]

            if not contains_profanity(content):
                continue

            print(f"  ⚠️  Profanity detected in post by {author}! Sending to Groq...")

            bullying_summary = ask_groq(content, author)

            send_discord_report(
                post_time        = published,
                author           = author,
                profanity        = True,
                bullying_summary = bullying_summary,
                post_url         = link,
            )


def main():
    # Check that credentials are set
    if not DISCORD_WEBHOOK_URL or not GROQ_API_KEY:
        print("❌ ERROR: Missing environment variables!")
        print("   Make sure DISCORD_WEBHOOK_URL and GROQ_API_KEY are set in Railway's Variables tab.")
        return

    print("=" * 50)
    print("  Coastal Anarchy Forum Moderation Bot")
    print("=" * 50)
    print(f"  Watching: {RSS_FEED_URL}")
    print(f"  Checking every {CHECK_INTERVAL} seconds")
    print("  Press Ctrl+C to stop.\n")

    print("Initial scan (loading existing threads to avoid re-flagging)...")
    try:
        feed = feedparser.parse(RSS_FEED_URL)
        for entry in feed.entries:
            post_id = entry.get("id", entry.get("link", ""))
            seen_post_ids.add(post_id)
        print(f"  Loaded {len(seen_post_ids)} existing threads. Now monitoring for new ones.\n")
    except Exception as e:
        print(f"  Warning: Could not load existing threads: {e}\n")

    while True:
        check_feed()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
