#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hr_poller.py

Polls the MLB Stats API every 1 minute, 24/7.
Sends an email notification whenever a new HR is detected,
triggers hr_excel_builder.py to update the Excel output,
and posts to the Going Yard & Drinking Hard backend webhook.
"""

import json
import os
import sys
import time
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import requests

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %I:%M %p",
    stream=sys.stdout
)
log = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")

class TimestampedOutput:
    def write(self, msg):
        if msg.strip():
            sys.__stdout__.write(
                f"[{datetime.now(tz=_EASTERN).strftime('%Y-%m-%d %I:%M %p ET')}]  {msg}"
            )
        else:
            sys.__stdout__.write(msg)
    def flush(self):
        sys.__stdout__.flush()

sys.stdout = TimestampedOutput()

# ---------------------------------------------------------------------------
# Player roster
# ---------------------------------------------------------------------------
PLAYERS = {
    "Alvarez":    {"mlb_id": 670541,  "team": "HOU", "full_name": "Yordan Alvarez"},
    "Harper":     {"mlb_id": 547180,  "team": "PHI", "full_name": "Bryce Harper"},
    "Rice":       {"mlb_id": 700250,  "team": "NYY", "full_name": "Ben Rice"},
    "Chisholm":   {"mlb_id": 665862,  "team": "NYY", "full_name": "Jazz Chisholm Jr."},
    "Schwarber":  {"mlb_id": 656941,  "team": "PHI", "full_name": "Kyle Schwarber"},
    "Grisham":    {"mlb_id": 663757,  "team": "NYY", "full_name": "Trent Grisham"},
    "Judge":      {"mlb_id": 592450,  "team": "NYY", "full_name": "Aaron Judge"},
    "Diaz":       {"mlb_id": 673237,  "team": "HOU", "full_name": "Yanier Diaz"},
    "Garcia":     {"mlb_id": 666969,  "team": "PHI", "full_name": "Adolis Garcia"},
    "Volpe":      {"mlb_id": 683011,  "team": "NYY", "full_name": "Anthony Volpe"},
    "Dominguez":  {"mlb_id": 691176,  "team": "NYY", "full_name": "Jasson Dominguez"},
    "Turner":     {"mlb_id": 607208,  "team": "PHI", "full_name": "Trea Turner"},
    "Wells":      {"mlb_id": 669224,  "team": "NYY", "full_name": "Austin Wells"},
    "McMahon":    {"mlb_id": 641857,  "team": "NYY", "full_name": "Ryan McMahon"},
}

# ---------------------------------------------------------------------------
# Matchup lookup
# ---------------------------------------------------------------------------
PLAYER_MATCHUP = {
    "Diaz":      ("frank",  "i_drink"),
    "Alvarez":   ("frank",  "you_drink"),
    "Garcia":    ("scott",  "i_drink"),
    "Harper":    ("scott",  "you_drink"),
    "Volpe":     ("tyler",  "i_drink"),
    "Rice":      ("tyler",  "you_drink"),
    "Dominguez": ("ned",    "i_drink"),
    "Chisholm":  ("ned",    "you_drink"),
    "Turner":    ("ryan",   "i_drink"),
    "Schwarber": ("ryan",   "you_drink"),
    "Grisham":   ("steve",  "you_drink"),
    "Wells":     ("steve",  "i_drink"),
    "Judge":     ("dan",    "you_drink"),
    "McMahon":   ("dan",    "i_drink"),
}

TRACKED_TEAMS = {p["team"] for p in PLAYERS.values()}

# ---------------------------------------------------------------------------
# Email config
# ---------------------------------------------------------------------------
os.environ["EMAIL_USER"] = "stephengaffney7@gmail.com"
os.environ["EMAIL_PASS"] = "bxbu nimx kull tjnq"
NOTIFY_TO  = ["stephengaffney7@gmail.com"] #, "Danmye01@gmail.com", "sllehrfeld@gmail.com", "frnkalaniz@gmail.com", "Ralecw11@gmail.com", "tylerfichtelberg@gmail.com", "nedheyman@gmail.com"
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

# ---------------------------------------------------------------------------
# Backend webhook config
# ---------------------------------------------------------------------------
BACKEND_URL    = "https://hr-game-production-140c.up.railway.app"
WEBHOOK_SECRET = "gyard_secret_2026"

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
HISTORY_FILE          = "last_results.json"
GAME_LOG_FILE         = "game_log.json"
POLL_INTERVAL_SECONDS = 60   # 1 minute

EASTERN  = ZoneInfo("America/New_York")
MLB_BASE = "https://statsapi.mlb.com/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_history(data: dict):
    clean = {k: int(v) for k, v in data.items() if v is not None}
    with open(HISTORY_FILE, "w") as f:
        json.dump(clean, f, indent=2)


def get_season_hrs(mlb_id: int) -> int | None:
    """Return current season HR total from MLB Stats API, or None on error."""
    season = datetime.now().year
    url = (
        f"{MLB_BASE}/people/{mlb_id}/stats"
        f"?stats=season&group=hitting&season={season}"
    )
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if splits:
            return int(splits[0]["stat"]["homeRuns"])
        else:
            return 0
    except Exception as e:
        print(f"  [WARN] Could not fetch stats for id={mlb_id}: {e} — will retry next cycle")
    return None


def send_email(subject: str, body: str):
    """Send a plain-text email via Gmail SMTP."""
    if not EMAIL_USER or not EMAIL_PASS:
        print("  [EMAIL] EMAIL_USER / EMAIL_PASS not set — skipping email.")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_USER
        msg["To"]      = ", ".join(NOTIFY_TO)
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, NOTIFY_TO, msg.as_string())
        print(f"  [EMAIL] Sent: {subject}")
    except Exception as e:
        print(f"  [EMAIL] Failed to send email: {e}")


def notify_webhook(key: str, player_info: dict, old_hrs: int, new_hrs: int):
    """POST the HR event to the Going Yard backend so it updates Supabase and sends push notifications."""
    try:
        r = requests.post(
            f"{BACKEND_URL}/webhook/hr",
            json={
                "player_key": key,
                "full_name":  player_info["full_name"],
                "team":       player_info["team"],
                "old_hrs":    old_hrs,
                "new_hrs":    new_hrs,
            },
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            timeout=10
        )
        if r.status_code == 201:
            print(f'''  [WEBHOOK] Posted to Going Yard app''')
        else:
            print(f"  [WEBHOOK] Unexpected response {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  [WEBHOOK] Failed to post: {e}")


def append_game_log(key: str, player_info: dict, old_hrs: int, new_hrs: int):
    """Append a HR event to the persistent game log."""
    log = []
    if os.path.exists(GAME_LOG_FILE):
        with open(GAME_LOG_FILE) as f:
            log = json.load(f)
    entry = {
        "timestamp":  datetime.now(tz=EASTERN).strftime("%Y-%m-%d %I:%M %p ET"),
        "player_key": key,
        "full_name":  player_info["full_name"],
        "team":       player_info["team"],
        "old_hrs":    old_hrs,
        "new_hrs":    new_hrs,
        "gain":       new_hrs - old_hrs,
    }
    log.append(entry)
    with open(GAME_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def notify_hr(key: str, player_info: dict, old_hrs: int, new_hrs: int):
    append_game_log(key, player_info, old_hrs, new_hrs)

    name      = player_info["full_name"]
    count     = new_hrs - old_hrs
    hr_word   = "home run" if count == 1 else "home runs"
    beer_word = "beer"     if count == 1 else "beers"

    drinker, drink_side = PLAYER_MATCHUP.get(key, ("N/A", "N/A"))
    verb = "drink" if drink_side == "i_drink" else "assign"

    subject = f"\u26be HR Alert: {name} hit {count} {hr_word}!"
    body = (
        f"{drinker.capitalize()} must {verb} {count} {beer_word} within 24 hours!\n\n"
        f"{name} just hit {count} {hr_word}!\n\n"
        f"Season total: {old_hrs} -> {new_hrs}\n\n"
        f"Timer Started: {datetime.now(tz=EASTERN).strftime('%Y-%m-%d %I:%M %p ET')}\n"
    )
    if drink_side == "you_drink":
        body += (
            f"\n{drinker.capitalize()}, open the Going Yard app to assign this drink.\n"
        )

    send_email(subject, body)
    notify_webhook(key, player_info, old_hrs, new_hrs)


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def poll_once(history: dict) -> dict:
    """
    Fetch current HRs for all tracked players, notify on changes.
    Reads last_results.json fresh from disk before each player check.
    """
    any_updated = False

    for key, info in PLAYERS.items():
        new_hrs = get_season_hrs(info["mlb_id"])
        if new_hrs is None:
            continue

        disk_history = load_history()
        old_hrs = disk_history.get(key, history.get(key))
        try:
            old_hrs = int(old_hrs) if old_hrs is not None else None
        except (TypeError, ValueError):
            old_hrs = None

        if old_hrs is None:
            log_data = []
            if os.path.exists(GAME_LOG_FILE):
                with open(GAME_LOG_FILE) as f:
                    log_data = json.load(f)
            prior_events = [e for e in log_data if e.get("player_key") == key]
            last_logged  = prior_events[-1]["new_hrs"] if prior_events else 0

            if new_hrs > last_logged:
                # Only fire a missed-HR notification if the last logged event
                # happened today — avoids creating stale drink_log entries with
                # wrong timestamps when the poller restarts after sleeping overnight.
                last_event_date = None
                if prior_events:
                    try:
                        last_ts = prior_events[-1].get("timestamp", "")
                        last_event_date = last_ts[:10]  # "YYYY-MM-DD"
                    except Exception:
                        pass

                today_str = datetime.now(tz=EASTERN).strftime("%Y-%m-%d")

                if last_event_date == today_str or not prior_events:
                    print(f"  {info['full_name']:25s}  *** MISSED HR: {last_logged} -> {new_hrs} ***")
                    notify_hr(key, info, last_logged, new_hrs)
                else:
                    print(f"  {info['full_name']:25s}  *** STALE MISSED HR (not today) — skipping notify: {last_logged} -> {new_hrs} ***")
                    print(f"    Last event was {last_event_date}, today is {today_str} — update history only")
            else:
                print(f"  {info['full_name']:25s}  First reading: {new_hrs} HRs")

            history[key] = new_hrs
            save_history(history)
            any_updated = True

        elif new_hrs > old_hrs:
            print(f"  {info['full_name']:25s}  *** NEW HR: {old_hrs} -> {new_hrs} ***")
            notify_hr(key, info, old_hrs, new_hrs)
            history[key] = new_hrs
            save_history(history)
            any_updated = True

        else:
            print(f"  {info['full_name']:25s}  {new_hrs} HRs (no change)")

    return history


def main():
    print("=" * 60)
    print("MLB HR Poller -- starting up")
    print(f"Tracking {len(PLAYERS)} players across teams: {sorted(TRACKED_TEAMS)}")
    print(f"Polling every {POLL_INTERVAL_SECONDS} seconds, 24/7")
    print(f"Webhook: {BACKEND_URL}")
    print("=" * 60)

    history = load_history()

    while True:
        now_et = datetime.now(tz=EASTERN)
        print(f"\n[{now_et.strftime('%Y-%m-%d %I:%M %p ET')}] Checking...")
        history = poll_once(history)
        time.sleep(POLL_INTERVAL_SECONDS)


main()
