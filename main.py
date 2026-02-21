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
# TRACKING - keeps track of individual posts we've already seen
# Key: (thread_url, author, content_snippet)
# ============================================================

seen_posts = set()


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

    for td in soup.find_all("td"):
        text = td.get_text(separator=" ", strip=True)

        if len(text) < 20:
            continue

        if "Post by" not in text:
            continue

        # Extract author from "Post by X on date"
        author = "Unknown"
        try:
            after = text.split("Post by")[1]
            author = after.split("on")[0].strip()
        except Exception:
            pass

        # Extract content after the post header
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
    """Fetches the RSS feed and scrapes all threads for new posts."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking feed...")

    try:
        feed = feedparser.parse(RSS_FEED_URL)
    except Exception as e:
        print(f"  ❌ Could not fetch RSS feed: {e}")
        return

    for entry in feed.entries:
        link      = entry.get("link", "")
        published = entry.get("published", "Unknown time")
        title     = entry.get("title", "No title")

        # Always scrape every thread to catch new replies
        posts = scrape_thread(link)

        for post in posts:
            author  = post["author"]
            content = post["content"]

            # Use a fingerprint to uniquely identify this post
            fingerprint = (link, author, content[:100])

            if fingerprint in seen_posts:
                continue

            # It's a new post - add to seen
            seen_posts.add(fingerprint)

            if not contains_profanity(content):
                continue

            print(f"  ⚠️  Profanity detected in post by {author} in '{title}'! Sending to Groq...")

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

    # Initial scan - load all existing posts so we don't re-flag old content
    print("Initial scan (loading existing posts to avoid re-flagging)...")
    try:
        feed = feedparser.parse(RSS_FEED_URL)
        count = 0
        for entry in feed.entries:
            link = entry.get("link", "")
            posts = scrape_thread(link)
            for post in posts:
                fingerprint = (link, post["author"], post["content"][:100])
                seen_posts.add(fingerprint)
                count += 1
        print(f"  Loaded {count} existing posts across {len(feed.entries)} threads. Now monitoring for new ones.\n")
    except Exception as e:
        print(f"  Warning: Could not load existing posts: {e}\n")

    while True:
        check_feed()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
