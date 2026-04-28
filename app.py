#!/usr/bin/env python3
"""
backend/app.py

Going Yard & Drinking Hard — Flask Backend API
Receives HR events from hr_poller.py, stores in Supabase,
and sends Web Push notifications to all subscribed users.

Deploy to Railway:
  1. Push this folder to GitHub
  2. Connect repo to Railway
  3. Set environment variables in Railway dashboard
"""

import os
import json
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from pywebpush import webpush, WebPushException

app = Flask(__name__)
CORS(app, origins=["https://going-yard.vercel.app", "http://localhost:3000"])

# ---------------------------------------------------------------------------
# Config — set these as environment variables in Railway
# ---------------------------------------------------------------------------
SUPABASE_URL          = os.environ.get("SUPABASE_URL", "https://rhqyfjikjkwrzzhttuwq.supabase.co")
SUPABASE_SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
VAPID_PRIVATE_KEY     = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY      = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_EMAIL           = os.environ.get("VAPID_EMAIL", "mailto:stephengaffney7@gmail.com")
WEBHOOK_SECRET        = os.environ.get("WEBHOOK_SECRET", "gyard_secret_2026")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ---------------------------------------------------------------------------
# Player → user matchup (mirrors hr_poller.py)
# ---------------------------------------------------------------------------
PLAYER_MATCHUP = {
    "Diaz":      ("frank",   "i_drink"),
    "Alvarez":   ("frank",   "you_drink"),
    "Garcia":    ("scott",   "i_drink"),
    "Harper":    ("scott",   "you_drink"),
    "Volpe":     ("tyler",   "i_drink"),
    "Rice":      ("tyler",   "you_drink"),
    "Dominguez": ("ned",     "i_drink"),
    "Chisholm":  ("ned",     "you_drink"),
    "Turner":    ("ryan",    "i_drink"),
    "Schwarber": ("ryan",    "you_drink"),
    "Grisham":   ("steve",   "you_drink"),
    "Wells":     ("steve",   "i_drink"),
    "Judge":     ("dan",     "you_drink"),
    "McMahon":   ("dan",     "i_drink"),
}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def require_webhook_secret(f):
    """Decorator to protect webhook endpoint from unauthorized callers."""
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = request.headers.get("X-Webhook-Secret")
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def require_auth(f):
    """Decorator to verify Supabase JWT on protected endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401
        token = auth_header.split(" ")[1]
        try:
            user = supabase.auth.get_user(token)
            request.user = user.user
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Push notification helper
# ---------------------------------------------------------------------------

def send_push_to_all(title: str, body: str, data: dict = None):
    """Send a Web Push notification to all subscribed users."""
    if not VAPID_PRIVATE_KEY:
        print("[PUSH] VAPID_PRIVATE_KEY not set — skipping push")
        return

    try:
        subs = supabase.table("push_subscriptions").select("*").execute()
    except Exception as e:
        print(f"[PUSH] Could not fetch subscriptions: {e}")
        return

    payload = json.dumps({
        "title": title,
        "body":  body,
        "data":  data or {}
    })

    for sub in subs.data:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh"],
                        "auth":   sub["auth_key"],
                    }
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": VAPID_EMAIL,
                    "exp": int(datetime.now(timezone.utc).timestamp()) + 86400
                }
            )
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                # Subscription expired — remove it
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
            else:
                print(f"[PUSH] Failed for {sub['username']}: {e}")


# ---------------------------------------------------------------------------
# Webhook — called by hr_poller.py on every new HR
# ---------------------------------------------------------------------------

@app.route("/webhook/hr", methods=["POST"])
@require_webhook_secret
def hr_webhook():
    """
    Receives HR event from hr_poller.py.
    Stores in hr_events + drink_log, sends push notifications.

    Expected JSON:
    {
        "player_key": "Judge",
        "full_name": "Aaron Judge",
        "team": "NYY",
        "old_hrs": 3,
        "new_hrs": 4,
        "drinker": "dan",
        "drink_type": "you_drink"
    }
    """
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400

    player_key = data.get("player_key")
    full_name  = data.get("full_name")
    team       = data.get("team")
    old_hrs    = data.get("old_hrs")
    new_hrs    = data.get("new_hrs")
    drinker, drink_type = PLAYER_MATCHUP.get(player_key, ("unknown", "unknown"))
    count      = new_hrs - old_hrs
    hr_word    = "home run" if count == 1 else "home runs"
    beer_word  = "beer" if count == 1 else "beers"

    # Insert HR event
    try:
        event_res = supabase.table("hr_events").insert({
            "player_key": player_key,
            "full_name":  full_name,
            "team":       team,
            "old_hrs":    old_hrs,
            "new_hrs":    new_hrs,
            "drink_type": drink_type,
            "drinker":    drinker,
        }).execute()
        event_id = event_res.data[0]["id"]
    except Exception as e:
        return jsonify({"error": f"Failed to insert hr_event: {e}"}), 500

    # Insert drink log entry
    try:
        supabase.table("drink_log").insert({
            "hr_event_id": event_id,
            "event_date":  datetime.now().strftime("%Y-%m-%d"),
            "username":    drinker,
            "mlb_player":  full_name,
            "drink_type":  drink_type,
            "given_to":    None,   # filled in later if you_drink and assigned
        }).execute()
    except Exception as e:
        print(f"[DB] Failed to insert drink_log: {e}")

    # Push notification
    if drink_type == "i_drink":
        push_title = f"⚾ {full_name} went yard!"
        push_body  = f"{drinker.capitalize()} drinks {count} {beer_word}!"
    else:
        push_title = f"⚾ {full_name} went yard!"
        push_body  = f"{drinker.capitalize()} must assign {count} {beer_word}!"

    send_push_to_all(push_title, push_body, {
        "event_id":   event_id,
        "player_key": player_key,
        "drink_type": drink_type,
        "drinker":    drinker,
    })

    return jsonify({"success": True, "event_id": event_id}), 201


# ---------------------------------------------------------------------------
# Drink assignment
# ---------------------------------------------------------------------------

@app.route("/assign", methods=["POST"])
@require_auth
def assign_drink():
    """
    Assigns a you_drink to another user.
    Only the matched drinker can assign, and only for their you_drink player.

    Expected JSON:
    {
        "hr_event_id": 42,
        "assignee": "scott",
        "message": "Scotty boy ur up 🍺"
    }
    """
    data       = request.json
    hr_event_id = data.get("hr_event_id")
    assignee   = data.get("assignee")
    message    = data.get("message", "")

    # Get the HR event
    try:
        event_res = supabase.table("hr_events").select("*").eq("id", hr_event_id).single().execute()
        event = event_res.data
    except Exception:
        return jsonify({"error": "HR event not found"}), 404

    # Verify this is a you_drink event
    if event["drink_type"] != "you_drink":
        return jsonify({"error": "This is not a you_drink event"}), 400

    # Verify the requesting user is the matched drinker
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    if username.lower() != event["drinker"].lower():
        return jsonify({"error": "Only the matched player can assign this drink"}), 403

    # Check not already assigned
    existing = supabase.table("drink_assignments").select("id").eq("hr_event_id", hr_event_id).execute()
    if existing.data:
        return jsonify({"error": "Drink already assigned"}), 400

    # Create assignment
    try:
        assign_res = supabase.table("drink_assignments").insert({
            "hr_event_id": hr_event_id,
            "assigner":    username,
            "assignee":    assignee,
            "message":     message,
            "status":      "pending",
        }).execute()
        assignment_id = assign_res.data[0]["id"]
    except Exception as e:
        return jsonify({"error": f"Failed to create assignment: {e}"}), 500

    # Update drink_log given_to
    try:
        supabase.table("drink_log").update({"given_to": assignee}).eq("hr_event_id", hr_event_id).execute()
    except Exception as e:
        print(f"[DB] Failed to update drink_log given_to: {e}")

    # Push notification
    send_push_to_all(
        f"🍺 Drink Assigned!",
        f"{username.capitalize()} assigned a drink to {assignee.capitalize()}! \"{message}\"",
        {"type": "assignment", "assignment_id": assignment_id}
    )

    return jsonify({"success": True, "assignment_id": assignment_id}), 201


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@app.route("/comments", methods=["POST"])
@require_auth
def add_comment():
    data        = request.json
    hr_event_id = data.get("hr_event_id")
    body        = data.get("body", "").strip()

    if not body:
        return jsonify({"error": "Comment body required"}), 400

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        res = supabase.table("comments").insert({
            "hr_event_id": hr_event_id,
            "user_id":     str(request.user.id),
            "username":    username,
            "body":        body,
        }).execute()
        return jsonify(res.data[0]), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

@app.route("/likes", methods=["POST"])
@require_auth
def toggle_like():
    data        = request.json
    target_type = data.get("target_type")   # hr_event | comment | assignment
    target_id   = data.get("target_id")

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    # Check if already liked
    existing = supabase.table("likes").select("id").eq("user_id", str(request.user.id)).eq("target_type", target_type).eq("target_id", target_id).execute()

    if existing.data:
        # Unlike
        supabase.table("likes").delete().eq("id", existing.data[0]["id"]).execute()
        return jsonify({"liked": False}), 200
    else:
        # Like
        supabase.table("likes").insert({
            "user_id":     str(request.user.id),
            "username":    username,
            "target_type": target_type,
            "target_id":   target_id,
        }).execute()
        return jsonify({"liked": True}), 200


# ---------------------------------------------------------------------------
# Push subscription management
# ---------------------------------------------------------------------------

@app.route("/push/subscribe", methods=["POST"])
@require_auth
def subscribe_push():
    data     = request.json
    endpoint = data.get("endpoint")
    p256dh   = data.get("keys", {}).get("p256dh")
    auth_key = data.get("keys", {}).get("auth")

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        supabase.table("push_subscriptions").upsert({
            "user_id":  str(request.user.id),
            "username": username,
            "endpoint": endpoint,
            "p256dh":   p256dh,
            "auth_key": auth_key,
        }, on_conflict="endpoint").execute()
        return jsonify({"success": True}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/push/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    return jsonify({"key": VAPID_PUBLIC_KEY}), 200


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "app": "Going Yard & Drinking Hard"}), 200


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
