#!/usr/bin/env python3
"""
backend/app.py

Going Yard & Drinking Hard — Flask Backend API
Receives HR events from hr_poller.py, stores in Supabase,
and sends Web Push notifications to all subscribed users.
"""

import os
import json
import random
from datetime import datetime, timezone, timedelta
from functools import wraps

HR_SLOGANS = [
    "Gone! See ya!",
    "That ball is OUTTA HERE!",
    "No doubt about it — GONE!",
    "He got ALL of that one!",
    "That one left the zip code!",
    "He tattooed that baseball!",
    "DEEP to center — it is GONE!",
    "Dinger!",
    "Absolute Tank!",
    "See ya, ball!",
]

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from pywebpush import webpush, WebPushException

app = Flask(__name__)
CORS(app, origins=["https://going-yard-frontend.vercel.app", "http://localhost:3000"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL          = os.environ.get("SUPABASE_URL", "https://rhqyfjikjkwrzzhttuwq.supabase.co")
SUPABASE_SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
VAPID_PRIVATE_KEY     = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY      = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_EMAIL           = os.environ.get("VAPID_EMAIL", "mailto:stephengaffney7@gmail.com")
WEBHOOK_SECRET        = os.environ.get("WEBHOOK_SECRET", "gyard_secret_2026")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ---------------------------------------------------------------------------
# Player → user matchup
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

LATE_HOURS = 24  # hours before a drink is considered late


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def require_webhook_secret(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = request.headers.get("X-Webhook-Secret")
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def require_auth(f):
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
    if not VAPID_PRIVATE_KEY:
        print("[PUSH] VAPID_PRIVATE_KEY not set — skipping push")
        return

    try:
        subs = supabase.table("push_subscriptions").select("*").execute()
    except Exception as e:
        print(f"[PUSH] Could not fetch subscriptions: {e}")
        return

    if not subs.data:
        print("[PUSH] No subscriptions found")
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
                },
                content_encoding="aes128gcm",
            )
            print(f"[PUSH] Sent to {sub['username']}")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
                print(f"[PUSH] Removed expired subscription for {sub['username']}")
            else:
                print(f"[PUSH] Failed for {sub['username']}: {e}")
        except Exception as e:
            print(f"[PUSH] Unexpected error for {sub['username']}: {e}")


# ---------------------------------------------------------------------------
# Targeted push — send only to specific usernames, excluding the actor
# ---------------------------------------------------------------------------

def send_push_to_users(usernames: list, title: str, body: str, exclude: str = None, data: dict = None):
    if not VAPID_PRIVATE_KEY:
        return
    if not usernames:
        return

    targets = [u.lower() for u in usernames if u and u.lower() != (exclude or "").lower()]
    if not targets:
        return

    try:
        subs = supabase.table("push_subscriptions").select("*").in_("username", targets).execute()
    except Exception as e:
        print(f"[PUSH] Could not fetch subscriptions: {e}")
        return

    if not subs.data:
        return

    payload = json.dumps({"title": title, "body": body, "data": data or {}})

    for sub in subs.data:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth_key"]},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": VAPID_EMAIL,
                    "exp": int(datetime.now(timezone.utc).timestamp()) + 86400,
                },
                content_encoding="aes128gcm",
            )
            print(f"[PUSH] Sent to {sub['username']}")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
            else:
                print(f"[PUSH] Failed for {sub['username']}: {e}")
        except Exception as e:
            print(f"[PUSH] Unexpected error for {sub['username']}: {e}")


# ---------------------------------------------------------------------------
# Late status helper — call periodically or on fetch to mark overdue drinks
# ---------------------------------------------------------------------------

def refresh_late_statuses(notify: bool = True):
    """
    Mark overdue drinks as late and optionally send push notifications.

    Lateness is based on hr_triggered_at (exact HR timestamp) for i_drink
    and pending you_drink, and assigned_at for awaiting_approval you_drink.

    notify=True  → send push notifications for newly-late drinks (normal operation)
    notify=False → silent sweep only, used on poller restart to avoid
                   re-notifying for drinks that went late before today
    """
    now      = datetime.now(timezone.utc)
    today    = now.strftime("%Y-%m-%d")
    cutoff   = (now - timedelta(hours=LATE_HOURS)).isoformat()

    try:
        # Find pending/awaiting drinks whose clock has expired
        # We join through hr_events to get the real HR timestamp,
        # and check assigned_at for awaiting_approval rows
        one_week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        overdue_res = (
            supabase.table("drink_log")
            .select("id, hr_event_id, username, given_to, drink_type, mlb_player, hr_triggered_at, assigned_at, event_date")
            .in_("status", ["pending", "awaiting_approval"])
            .gte("event_date", one_week_ago)
            .execute()
        )
        if not overdue_res.data:
            return

        newly_late = []
        for row in overdue_res.data:
            if row["status"] == "awaiting_approval" and row.get("assigned_at"):
                # Clock starts from assigned_at for assigned you_drink
                clock_start = row.get("assigned_at")
            elif row.get("given_to") and row.get("assigned_at"):
                # Also use assigned_at if drink has been assigned
                clock_start = row.get("assigned_at")
            else:
                # Clock starts from HR timestamp for i_drink and unassigned you_drink
                clock_start = row.get("hr_triggered_at")

            if not clock_start:
                # Fall back to noon on event_date if timestamps missing
                clock_start = (row.get("event_date") or today) + "T12:00:00+00:00"

            try:
                start_dt = datetime.fromisoformat(clock_start.replace("Z", "+00:00"))
                age_hours = (now - start_dt).total_seconds() / 3600
            except Exception:
                age_hours = 0

            if age_hours >= LATE_HOURS:
                newly_late.append(row)

        if not newly_late:
            return

        ids = [r["id"] for r in newly_late]
        supabase.table("drink_log").update({"status": "late"}).in_("id", ids).execute()
        print(f"[LATE] Marked {len(ids)} drink(s) as late")

        if not notify:
            print("[LATE] Silent sweep — skipping notifications")
            return

        # Only notify for drinks that went late today (avoids spam on restart)
        for row in newly_late:
            clock_start = row.get("assigned_at") or row.get("hr_triggered_at") or (row.get("event_date", today) + "T12:00:00+00:00")
            try:
                start_dt   = datetime.fromisoformat(clock_start.replace("Z", "+00:00"))
                late_since  = start_dt + timedelta(hours=LATE_HOURS)
                # Only notify if the drink crossed the late threshold today
                if late_since.strftime("%Y-%m-%d") != today:
                    continue
            except Exception:
                continue

            drinker = (row.get("given_to") or row["username"]).capitalize()
            player  = row.get("mlb_player", "a player")
            send_push_to_all(
                "🔴 Late Drink!",
                f"{drinker} hasn't drank for {player}'s homer yet — 24 hours are up!",
                {"type": "late", "hr_event_id": row.get("hr_event_id")}
            )

    except Exception as e:
        print(f"[LATE] Error refreshing late statuses: {e}")


# ---------------------------------------------------------------------------
# Webhook — called by hr_poller.py on every new HR
# ---------------------------------------------------------------------------

@app.route("/webhook/hr", methods=["POST"])
@require_webhook_secret
def hr_webhook():
    """
    Receives HR event from hr_poller.py.
    Stores in hr_events + drink_log (status='pending'), sends push notifications.
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
        slogan = random.choice(HR_SLOGANS)
        event_res = supabase.table("hr_events").insert({
            "player_key": player_key,
            "full_name":  full_name,
            "team":       team,
            "old_hrs":    old_hrs,
            "new_hrs":    new_hrs,
            "drink_type": drink_type,
            "drinker":    drinker,
            "slogan":     slogan,
        }).execute()
        event_id = event_res.data[0]["id"]
    except Exception as e:
        return jsonify({"error": f"Failed to insert hr_event: {e}"}), 500

    # Insert drink log entry — always starts as 'pending'
    try:
        supabase.table("drink_log").insert({
            "hr_event_id":     event_id,
            "event_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "hr_triggered_at": datetime.now(timezone.utc).isoformat(),
            "username":        drinker,
            "mlb_player":      full_name,
            "drink_type":      drink_type,
            "given_to":        None,
            "status":          "pending",
        }).execute()
    except Exception as e:
        print(f"[DB] Failed to insert drink_log: {e}")

    # Push notification
    if drink_type == "i_drink":
        push_title = f"⚾ {full_name} went yard!"
        push_body  = f"{slogan} {drinker.capitalize()} drinks {count} {beer_word}!"
    else:
        push_title = f"⚾ {full_name} went yard!"
        push_body  = f"{slogan} {drinker.capitalize()} must assign {count} {beer_word}!"

    send_push_to_all(push_title, push_body, {
        "hr_event_id": event_id,
        "player_key":  player_key,
        "drink_type":  drink_type,
        "drinker":     drinker,
    })

    # Refresh any newly-late drinks while we're here — with notifications
    refresh_late_statuses(notify=True)

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
    After assignment the drink_log status moves to 'awaiting_approval'.
    """
    data        = request.json
    hr_event_id = data.get("hr_event_id")
    assignee    = data.get("assignee")
    message     = data.get("message", "")

    try:
        event_res = supabase.table("hr_events").select("*").eq("id", hr_event_id).single().execute()
        event = event_res.data
    except Exception:
        return jsonify({"error": "HR event not found"}), 404

    if event["drink_type"] != "you_drink":
        return jsonify({"error": "This is not a you_drink event"}), 400

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    if username.lower() != event["drinker"].lower():
        return jsonify({"error": "Only the matched player can assign this drink"}), 403

    existing = supabase.table("drink_assignments").select("id").eq("hr_event_id", hr_event_id).execute()
    if existing.data:
        return jsonify({"error": "Drink already assigned"}), 400

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

    # Update drink_log: set given_to and move to awaiting_approval
    try:
        supabase.table("drink_log").update({
            "given_to":    assignee,
            "status":      "awaiting_approval",
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }).eq("hr_event_id", hr_event_id).execute()
    except Exception as e:
        print(f"[DB] Failed to update drink_log: {e}")

    # Notify everyone except the assigner
    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        targets = [s["username"] for s in (subs.data or [])]
        send_push_to_users(
            targets,
            "🍺 Drink Assigned!",
            f"{username.capitalize()} assigned a drink to {assignee.capitalize()}! \"{message}\"",
            exclude=username,
            data={"type": "assignment", "assignment_id": assignment_id, "hr_event_id": hr_event_id}
        )
    except Exception as e:
        print(f"[PUSH] Assignment notify failed: {e}")

    return jsonify({"success": True, "assignment_id": assignment_id}), 201


# ---------------------------------------------------------------------------
# Drink approval  ← NEW
# ---------------------------------------------------------------------------

@app.route("/drinks/approve", methods=["POST"])
@require_auth
def approve_drink():
    """
    Marks a drink as completed (approved by another player).

    Rules:
      - The approver must NOT be the person who was supposed to drink.
        • For i_drink:   the drinker is event.drinker
        • For you_drink: the drinker is drink_log.given_to (the assignee)
      - Only one approval needed; idempotent if already completed.

    Expected JSON: { "drink_log_id": <int> }
    """
    data         = request.json
    drink_log_id = data.get("drink_log_id")

    if not drink_log_id:
        return jsonify({"error": "drink_log_id required"}), 400

    # Fetch the drink log entry
    try:
        dl_res = supabase.table("drink_log").select("*").eq("id", drink_log_id).single().execute()
        dl = dl_res.data
    except Exception:
        return jsonify({"error": "Drink log entry not found"}), 404

    if dl["status"] in ("completed", "completed_late"):
        return jsonify({"success": True, "message": "Already completed"}), 200

    # Who is the actual drinker for this entry?
    # For i_drink → the drinker field; for you_drink → the given_to field
    actual_drinker = (dl.get("given_to") or dl["username"]).lower()

    # Get the approver's username
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    approver = profile_res.data["username"].lower()

    if approver == actual_drinker:
        return jsonify({"error": "You cannot approve your own drink"}), 403

    # Mark completed — if it was already late, mark as completed_late
    final_status = "completed_late" if dl["status"] == "late" else "completed"
    try:
        supabase.table("drink_log").update({
            "status":      final_status,
            "approved_by": approver,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", drink_log_id).execute()
    except Exception as e:
        return jsonify({"error": f"Failed to approve drink: {e}"}), 500

    # Also update the drink_assignments status if this is a you_drink
    try:
        if dl["drink_type"] == "you_drink" and dl.get("hr_event_id"):
            supabase.table("drink_assignments").update({
                "status": "completed"
            }).eq("hr_event_id", dl["hr_event_id"]).execute()
    except Exception as e:
        print(f"[DB] Failed to update drink_assignment status: {e}")

    # Notify everyone except the approver
    drinker_display = actual_drinker.capitalize()
    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        targets = [s["username"] for s in (subs.data or [])]
        send_push_to_users(
            targets,
            "✅ Drink Confirmed!",
            f"{approver.capitalize()} approved {drinker_display}'s drink. Bottoms up! 🍺",
            exclude=approver,
            data={"type": "approval", "drink_log_id": drink_log_id, "hr_event_id": dl.get("hr_event_id")}
        )
    except Exception as e:
        print(f"[PUSH] Approval notify failed: {e}")

    return jsonify({"success": True, "approved_by": approver}), 200


# ---------------------------------------------------------------------------
# Late status refresh endpoint  ← NEW (callable by a cron or manually)
# ---------------------------------------------------------------------------

@app.route("/drinks/refresh-late", methods=["POST"])
@require_webhook_secret
def trigger_late_refresh():
    """Webhook-protected endpoint to force a late-status sweep."""
    refresh_late_statuses()
    return jsonify({"success": True}), 200


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
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Notify drinker(s) — not the commenter themselves
    try:
        event = supabase.table("hr_events").select("drinker, drink_type").eq("id", hr_event_id).single().execute().data
        dl_res = supabase.table("drink_log").select("given_to").eq("hr_event_id", hr_event_id).execute()
        dl    = dl_res.data[0] if dl_res.data else None
        notify_users = set()
        notify_users.add(event["drinker"])
        if event["drink_type"] == "you_drink" and dl and dl.get("given_to"):
            notify_users.add(dl["given_to"])
        send_push_to_users(
            list(notify_users),
            "💬 New Comment",
            f"{username.capitalize()} left a comment",
            exclude=username,
            data={"type": "comment", "hr_event_id": hr_event_id}
        )
    except Exception as e:
        print(f"[PUSH] Comment notify failed: {e}")

    return jsonify(res.data[0]), 201


@app.route("/comments/<int:comment_id>", methods=["DELETE"])
@require_auth
def delete_comment(comment_id):
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    # Verify the comment belongs to the requesting user
    try:
        comment = supabase.table("comments").select("username").eq("id", comment_id).single().execute()
    except Exception:
        return jsonify({"error": "Comment not found"}), 404

    if comment.data["username"].lower() != username.lower():
        return jsonify({"error": "You can only delete your own comments"}), 403

    supabase.table("comments").delete().eq("id", comment_id).execute()
    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

@app.route("/likes", methods=["POST"])
@require_auth
def toggle_like():
    data        = request.json
    target_type = data.get("target_type")
    target_id   = data.get("target_id")

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    existing = supabase.table("likes").select("id").eq("user_id", str(request.user.id)).eq("target_type", target_type).eq("target_id", target_id).execute()

    if existing.data:
        supabase.table("likes").delete().eq("id", existing.data[0]["id"]).execute()
        return jsonify({"liked": False}), 200

    supabase.table("likes").insert({
        "user_id":     str(request.user.id),
        "username":    username,
        "target_type": target_type,
        "target_id":   target_id,
    }).execute()

    # Notify relevant users for hr_event likes only
    if target_type == "hr_event":
        try:
            event = supabase.table("hr_events").select("drinker, drink_type").eq("id", target_id).single().execute().data
            dl_res = supabase.table("drink_log").select("given_to").eq("hr_event_id", target_id).execute()
            dl    = dl_res.data[0] if dl_res.data else None
            assignee = dl.get("given_to") if dl else None

            if event["drink_type"] == "i_drink":
                send_push_to_users(
                    [event["drinker"]],
                    "⚾ Cheers!",
                    f"{username.capitalize()} says cheers!",
                    exclude=username,
                    data={"type": "like", "hr_event_id": target_id}
                )
            else:
                send_push_to_users(
                    [event["drinker"]],
                    "⚾ Nice one!",
                    f"{username.capitalize()} says nice one!",
                    exclude=username,
                    data={"type": "like", "hr_event_id": target_id}
                )
                if assignee:
                    send_push_to_users(
                        [assignee],
                        "⚾ Bottoms up!",
                        f"{username.capitalize()} says bottoms up!",
                        exclude=username,
                        data={"type": "like", "hr_event_id": target_id}
                    )
        except Exception as e:
            print(f"[PUSH] Like notify failed: {e}")

    return jsonify({"liked": True}), 200


# ---------------------------------------------------------------------------
# Video upload notification
# ---------------------------------------------------------------------------

@app.route("/videos/notify", methods=["POST"])
@require_auth
def notify_video_upload():
    data        = request.json
    hr_event_id = data.get("hr_event_id")
    player_name = data.get("player_name", "")
    video_id    = data.get("video_id")      # chug_videos.id for direct deep link

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    uploader = profile_res.data["username"]

    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        targets = [s["username"] for s in (subs.data or [])]
        send_push_to_users(
            targets,
            "🎥 New Chug Video!",
            f"{uploader.capitalize()} uploaded their chug for {player_name}'s homer!",
            exclude=uploader,
            data={"type": "video", "hr_event_id": hr_event_id, "video_id": video_id}
        )
    except Exception as e:
        print(f"[PUSH] Video notify failed: {e}")

    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Video auto-cleanup — rolling cap of 10 most recent videos
# ---------------------------------------------------------------------------

@app.route("/videos/cleanup", methods=["POST"])
@require_auth
def cleanup_videos():
    """
    Called before each new video upload.
    Deletes the oldest video(s) from both Supabase Storage and chug_videos
    until the total count is below MAX_VIDEOS, making room for the new upload.
    """
    MAX_VIDEOS = 10

    try:
        # Fetch all videos ordered oldest first
        res = supabase.table("chug_videos").select("id, storage_path").order("created_at", desc=False).execute()
        videos = res.data or []
    except Exception as e:
        print(f"[CLEANUP] Failed to fetch videos: {e}")
        return jsonify({"error": str(e)}), 500

    # How many need to be deleted to bring count to MAX_VIDEOS - 1 (room for new one)
    to_delete_count = max(0, len(videos) - (MAX_VIDEOS - 1))

    if to_delete_count == 0:
        return jsonify({"success": True, "deleted": 0}), 200

    to_delete = videos[:to_delete_count]
    deleted   = 0

    for video in to_delete:
        # Delete from Supabase Storage
        try:
            supabase.storage.from_("chug-videos").remove([video["storage_path"]])
        except Exception as e:
            print(f"[CLEANUP] Storage delete failed for {video['storage_path']}: {e}")

        # Delete from chug_videos table
        try:
            supabase.table("chug_videos").delete().eq("id", video["id"]).execute()
            deleted += 1
            print(f"[CLEANUP] Deleted video id={video['id']}")
        except Exception as e:
            print(f"[CLEANUP] DB delete failed for id={video['id']}: {e}")

    return jsonify({"success": True, "deleted": deleted}), 200


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
    # Silent sweep — don't notify on health pings to avoid
    # re-notifying for drinks that went late before today on restart
    refresh_late_statuses(notify=False)
    return jsonify({"status": "ok", "app": "Going Yard & Drinking Hard"}), 200


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
