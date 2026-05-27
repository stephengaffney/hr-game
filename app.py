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
# Push notification helpers
# ---------------------------------------------------------------------------

def _send_push(sub: dict, payload: str):
    """Send a single push. Returns True on success, False on expiry, raises on other error."""
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
    )
    return True


def _push_subs_for_users(usernames: list) -> list:
    """Fetch push subscription rows for a list of usernames (case-insensitive)."""
    if not usernames:
        return []
    try:
        # Normalise to lowercase for comparison — the DB may store any casing.
        lower_targets = {u.lower() for u in usernames}
        # Fetch all subscriptions and filter client-side so casing mismatches
        # in the push_subscriptions table don't silently drop targeted pushes.
        res = supabase.table("push_subscriptions").select("*").execute()
        return [r for r in (res.data or []) if r["username"].lower() in lower_targets]
    except Exception as e:
        print(f"[PUSH] Could not fetch subscriptions: {e}")
        return []


def _bulk_push_prefs(usernames: list, notif_type: str) -> dict:
    """
    Bulk-fetch notification preferences for a list of usernames and a single type.
    Returns { username_lower: bool } — defaults to True if no row exists.
    """
    if not usernames or not notif_type:
        return {u.lower(): True for u in usernames}
    try:
        targets = [u.lower() for u in usernames]
        res = (supabase.table("notification_preferences")
               .select("username, enabled")
               .in_("username", targets)
               .eq("type", notif_type)
               .execute())
        saved = {r["username"].lower(): bool(r["enabled"]) for r in (res.data or [])}
        # Default missing users to True
        return {u: saved.get(u, True) for u in targets}
    except Exception:
        return {u.lower(): True for u in usernames}


def _user_wants_push(username: str, notif_type: str) -> bool:
    """Single-user preference check — used only when bulk fetch isn't practical."""
    try:
        res = (supabase.table("notification_preferences")
               .select("enabled")
               .eq("username", username.lower())
               .eq("type", notif_type)
               .execute())
        if res.data:
            return bool(res.data[0]["enabled"])
    except Exception:
        pass
    return True


def send_push_to_all(title: str, body: str, data: dict = None, notif_type: str = None):
    """Send push to all subscribed users (respecting their preferences)."""
    if not VAPID_PRIVATE_KEY:
        print("[PUSH] VAPID_PRIVATE_KEY not set — skipping push")
        return
    try:
        subs = supabase.table("push_subscriptions").select("*").execute()
    except Exception as e:
        print(f"[PUSH] Could not fetch subscriptions: {e}")
        return
    if not subs.data:
        return

    # Bulk-fetch preferences for all subscribers in one query
    usernames = [s["username"] for s in subs.data]
    prefs = _bulk_push_prefs(usernames, notif_type) if notif_type else {}

    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    for sub in subs.data:
        uname = sub["username"].lower()
        if notif_type and not prefs.get(uname, True):
            continue
        try:
            _send_push(sub, payload)
            print(f"[PUSH] Sent to {sub['username']}")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
                print(f"[PUSH] Removed expired subscription for {sub['username']}")
            else:
                print(f"[PUSH] Failed for {sub['username']}: {e}")
        except Exception as e:
            print(f"[PUSH] Unexpected error for {sub['username']}: {e}")


def send_push_to_users(usernames: list, title: str, body: str, exclude: str = None,
                       data: dict = None, notif_type: str = None):
    """Send push to specific usernames (respecting their preferences)."""
    if not VAPID_PRIVATE_KEY or not usernames:
        return
    targets = [u.lower() for u in usernames if u and u.lower() != (exclude or "").lower()]
    if not targets:
        return
    subs = _push_subs_for_users(targets)
    if not subs:
        return

    # Bulk-fetch preferences in one query
    prefs = _bulk_push_prefs(targets, notif_type) if notif_type else {}

    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    for sub in subs:
        uname = sub["username"].lower()
        if notif_type and not prefs.get(uname, True):
            continue
        try:
            _send_push(sub, payload)
            print(f"[PUSH] Sent to {sub['username']}")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
            else:
                print(f"[PUSH] Failed for {sub['username']}: {e}")
        except Exception as e:
            print(f"[PUSH] Unexpected error for {sub['username']}: {e}")


def send_push_targeted(targets_with_bodies: list, title: str, data: dict = None,
                       notif_type: str = None):
    """
    Send personalised push bodies to specific users.
    targets_with_bodies: list of (username, body_str) tuples.
    Respects push preferences. Deduplicates by username (first entry wins).
    """
    if not VAPID_PRIVATE_KEY:
        return
    seen = set()
    unique = []
    for username, body in targets_with_bodies:
        u = username.lower()
        if u not in seen:
            seen.add(u)
            unique.append((u, body))

    usernames = [u for u, _ in unique]
    subs = _push_subs_for_users(usernames)
    sub_map = {s["username"].lower(): s for s in subs}

    # Bulk-fetch preferences in one query
    prefs = _bulk_push_prefs(usernames, notif_type) if notif_type else {}

    for username, body in unique:
        if notif_type and not prefs.get(username, True):
            continue
        sub = sub_map.get(username)
        if not sub:
            continue
        payload = json.dumps({"title": title, "body": body, "data": data or {}})
        try:
            _send_push(sub, payload)
            print(f"[PUSH] Targeted sent to {username}")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                supabase.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
            else:
                print(f"[PUSH] Failed for {username}: {e}")
        except Exception as e:
            print(f"[PUSH] Unexpected error for {username}: {e}")


def write_notification(type: str, title: str, body: str, data: dict = None):
    """Insert a row into the notifications table."""
    try:
        supabase.table("notifications").insert({
            "type":  type,
            "title": title,
            "body":  body,
            "data":  data or {},
        }).execute()
    except Exception as e:
        print(f"[NOTIF] Failed to write notification: {e}")


# ---------------------------------------------------------------------------
# Helper: collect prior commenters on an hr_event or video
# ---------------------------------------------------------------------------

def get_prior_commenters(hr_event_id=None, video_id=None, exclude: str = None) -> set:
    """Return set of usernames who already top-level commented on this post, excluding `exclude`."""
    commenters = set()
    try:
        if hr_event_id:
            res = (supabase.table("comments")
                   .select("username")
                   .eq("hr_event_id", hr_event_id)
                   .is_("parent_comment_id", "null")
                   .execute())
        elif video_id:
            res = (supabase.table("comments")
                   .select("username")
                   .eq("video_id", video_id)
                   .is_("parent_comment_id", "null")
                   .execute())
        else:
            return commenters
        for row in (res.data or []):
            u = row["username"].lower()
            if u != (exclude or "").lower():
                commenters.add(u)
    except Exception as e:
        print(f"[COMMENT] Could not fetch prior commenters: {e}")
    return commenters


# ---------------------------------------------------------------------------
# Late status helper
# ---------------------------------------------------------------------------

def refresh_late_statuses(notify: bool = True):
    now      = datetime.now(timezone.utc)
    today    = now.strftime("%Y-%m-%d")

    try:
        one_week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        overdue_res = (
            supabase.table("drink_log")
            .select("id, hr_event_id, username, given_to, drink_type, mlb_player, hr_triggered_at, assigned_at, event_date, status")
            .in_("status", ["pending", "awaiting_approval"])
            .gte("event_date", one_week_ago)
            .execute()
        )
        if not overdue_res.data:
            return

        newly_late = []
        for row in overdue_res.data:
            if row["status"] == "awaiting_approval" and row.get("assigned_at"):
                clock_start = row.get("assigned_at")
            elif row.get("given_to") and row.get("assigned_at"):
                clock_start = row.get("assigned_at")
            else:
                clock_start = row.get("hr_triggered_at")
            if not clock_start:
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

        for row in newly_late:
            clock_start = row.get("assigned_at") or row.get("hr_triggered_at") or (row.get("event_date", today) + "T12:00:00+00:00")
            try:
                start_dt  = datetime.fromisoformat(clock_start.replace("Z", "+00:00"))
                late_since = start_dt + timedelta(hours=LATE_HOURS)
                if late_since.strftime("%Y-%m-%d") != today:
                    continue
            except Exception:
                continue

            drinker = (row.get("given_to") or row["username"]).capitalize()
            player  = row.get("mlb_player", "a player")
            send_push_to_all(
                "🔴 Late Drink!",
                f"{drinker} hasn't drank for {player}'s homer yet — 24 hours are up!",
                {"type": "late", "hr_event_id": row.get("hr_event_id")},
                notif_type="late",
            )
            write_notification("late", "🔴 Late Drink!",
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
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400

    player_key = data.get("player_key")
    full_name  = data.get("full_name")
    team       = data.get("team")
    old_hrs    = data.get("old_hrs")
    new_hrs    = data.get("new_hrs")
    drinker, drink_type = PLAYER_MATCHUP.get(player_key, ("unknown", "unknown"))
    count     = new_hrs - old_hrs
    beer_word = "beer" if count == 1 else "beers"

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
        # hr_events row exists but drink_log failed — the feed card would appear
        # with no approve button and no way to ever complete the drink.
        # Return 500 so the poller logs the failure and can be retried manually.
        return jsonify({"error": f"Failed to insert drink_log: {e}"}), 500

    if drink_type == "i_drink":
        push_body = f"{slogan} {drinker.capitalize()} drinks {count} {beer_word}!"
    else:
        push_body = f"{slogan} {drinker.capitalize()} must assign {count} {beer_word}!"

    push_title = f"⚾ {full_name} went yard!"
    send_push_to_all(push_title, push_body, {
        "hr_event_id": event_id,
        "player_key":  player_key,
        "drink_type":  drink_type,
        "drinker":     drinker,
    }, notif_type="hr")
    write_notification("hr", push_title, push_body, {
        "hr_event_id": event_id,
        "player_key":  player_key,
        "drink_type":  drink_type,
        "drinker":     drinker,
    })
    refresh_late_statuses(notify=True)
    return jsonify({"success": True, "event_id": event_id}), 201


# ---------------------------------------------------------------------------
# Drink assignment
# ---------------------------------------------------------------------------

@app.route("/assign", methods=["POST"])
@require_auth
def assign_drink():
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

    # Update drink_log FIRST — if this fails we haven't created the assignment row
    # yet, so the assigner can safely retry. Reversing this order would leave the
    # drink stuck (assignment row exists → duplicate check blocks retry, but
    # drink_log.given_to stays null forever).
    try:
        supabase.table("drink_log").update({
            "given_to":    assignee.lower(),   # always lowercase for consistent comparisons
            "status":      "awaiting_approval",
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }).eq("hr_event_id", hr_event_id).execute()
    except Exception as e:
        return jsonify({"error": f"Failed to update drink log: {e}"}), 500

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
        # drink_log is already updated — roll it back so the assigner can retry
        try:
            supabase.table("drink_log").update({
                "given_to":    None,
                "status":      "pending",
                "assigned_at": None,
            }).eq("hr_event_id", hr_event_id).execute()
        except Exception as rollback_err:
            print(f"[DB] Rollback failed after assignment insert error: {rollback_err}")
        return jsonify({"error": f"Failed to create assignment: {e}"}), 500

    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        targets = [s["username"] for s in (subs.data or [])]
        send_push_to_users(
            targets,
            "🍺 Drink Assigned!",
            f"{username.capitalize()} assigned a drink to {assignee.capitalize()}! \"{message}\"",
            exclude=username,
            data={"type": "assignment", "assignment_id": assignment_id, "hr_event_id": hr_event_id},
            notif_type="assignment",
        )
        write_notification("assignment", "🍺 Drink Assigned!",
            f"{username.capitalize()} assigned a drink to {assignee.capitalize()}! \"{message}\"",
            {"type": "assignment", "assignment_id": assignment_id, "hr_event_id": hr_event_id}
        )
    except Exception as e:
        print(f"[PUSH] Assignment notify failed: {e}")

    return jsonify({"success": True, "assignment_id": assignment_id}), 201


# ---------------------------------------------------------------------------
# Drink approval
# ---------------------------------------------------------------------------

@app.route("/drinks/approve", methods=["POST"])
@require_auth
def approve_drink():
    data         = request.json
    drink_log_id = data.get("drink_log_id")
    if not drink_log_id:
        return jsonify({"error": "drink_log_id required"}), 400

    try:
        dl_res = supabase.table("drink_log").select("*").eq("id", drink_log_id).single().execute()
        dl = dl_res.data
    except Exception:
        return jsonify({"error": "Drink log entry not found"}), 404

    if dl["status"] in ("completed", "completed_late"):
        return jsonify({"success": True, "message": "Already completed"}), 200

    actual_drinker = (dl.get("given_to") or dl["username"]).lower()
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    approver = profile_res.data["username"].lower()

    if approver == actual_drinker:
        return jsonify({"error": "You cannot approve your own drink"}), 403

    is_late = dl["status"] == "late"
    if not is_late:
        clock_start = None
        if dl["status"] == "awaiting_approval" and dl.get("assigned_at"):
            clock_start = dl.get("assigned_at")
        elif dl.get("given_to") and dl.get("assigned_at"):
            clock_start = dl.get("assigned_at")
        else:
            clock_start = dl.get("hr_triggered_at")
        if clock_start:
            try:
                start_dt = datetime.fromisoformat(clock_start.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - start_dt).total_seconds() / 3600 >= LATE_HOURS:
                    is_late = True
            except Exception:
                pass

    final_status = "completed_late" if is_late else "completed"
    try:
        supabase.table("drink_log").update({
            "status":      final_status,
            "approved_by": approver,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", drink_log_id).execute()
    except Exception as e:
        return jsonify({"error": f"Failed to approve drink: {e}"}), 500

    try:
        if dl["drink_type"] == "you_drink" and dl.get("hr_event_id"):
            supabase.table("drink_assignments").update({"status": "completed"}).eq("hr_event_id", dl["hr_event_id"]).execute()
    except Exception as e:
        print(f"[DB] Failed to update drink_assignment status: {e}")

    drinker_display = actual_drinker.capitalize()
    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        targets = [s["username"] for s in (subs.data or [])]
        send_push_to_users(
            targets,
            "✅ Drink Confirmed!",
            f"{approver.capitalize()} approved {drinker_display}'s drink. Bottoms up! 🍺",
            exclude=approver,
            data={"type": "approval", "drink_log_id": drink_log_id, "hr_event_id": dl.get("hr_event_id")},
            notif_type="approval",
        )
        write_notification("approval", "✅ Drink Confirmed!",
            f"{approver.capitalize()} approved {drinker_display}'s drink. Bottoms up! 🍺",
            {"type": "approval", "drink_log_id": drink_log_id, "hr_event_id": dl.get("hr_event_id")}
        )
    except Exception as e:
        print(f"[PUSH] Approval notify failed: {e}")

    return jsonify({"success": True, "approved_by": approver}), 200


# ---------------------------------------------------------------------------
# Late status refresh endpoint
# ---------------------------------------------------------------------------

@app.route("/drinks/refresh-late", methods=["POST"])
@require_webhook_secret
def trigger_late_refresh():
    refresh_late_statuses()
    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Comments — feed cards
# ---------------------------------------------------------------------------

@app.route("/comments", methods=["POST"])
@require_auth
def add_comment():
    data             = request.json
    hr_event_id      = data.get("hr_event_id")
    body             = data.get("body", "").strip()
    parent_comment_id = data.get("parent_comment_id")  # None for top-level

    if not body:
        return jsonify({"error": "Comment body required"}), 400

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    insert_payload = {
        "hr_event_id":       hr_event_id,
        "user_id":           str(request.user.id),
        "username":          username,
        "body":              body,
        "parent_comment_id": parent_comment_id,
    }

    try:
        res = supabase.table("comments").insert(insert_payload).execute()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        event = supabase.table("hr_events").select("drinker, drink_type").eq("id", hr_event_id).single().execute().data
        dl_res = supabase.table("drink_log").select("given_to").eq("hr_event_id", hr_event_id).execute()
        dl = dl_res.data[0] if dl_res.data else None
        assignee = dl.get("given_to") if dl else None

        notify_set = set()
        # Always notify the drinker and assignee
        notify_set.add(event["drinker"].lower())
        if event["drink_type"] == "you_drink" and assignee:
            notify_set.add(assignee.lower())

        if parent_comment_id:
            # Reply: also notify the parent commenter
            try:
                parent_res = supabase.table("comments").select("username").eq("id", parent_comment_id).single().execute()
                notify_set.add(parent_res.data["username"].lower())
            except Exception:
                pass
        else:
            # Top-level: notify all prior commenters on this post
            prior = get_prior_commenters(hr_event_id=hr_event_id, exclude=username)
            notify_set.update(prior)

        notify_set.discard(username.lower())

        notif_body = f"{username.capitalize()} replied to a comment" if parent_comment_id else f"{username.capitalize()} left a comment"

        send_push_to_users(
            list(notify_set),
            "💬 New Comment",
            notif_body,
            exclude=username,
            data={"type": "comment", "hr_event_id": hr_event_id},
            notif_type="comment",
        )
        write_notification("comment", "💬 New Comment", notif_body,
            {"type": "comment", "hr_event_id": hr_event_id}
        )
    except Exception as e:
        print(f"[PUSH] Comment notify failed: {e}")

    return jsonify(res.data[0]), 201


@app.route("/comments/<int:comment_id>", methods=["DELETE"])
@require_auth
def delete_comment(comment_id):
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        comment = supabase.table("comments").select("username").eq("id", comment_id).single().execute()
    except Exception:
        return jsonify({"error": "Comment not found"}), 404

    if comment.data["username"].lower() != username.lower():
        return jsonify({"error": "You can only delete your own comments"}), 403

    supabase.table("comments").delete().eq("id", comment_id).execute()
    return jsonify({"success": True}), 200


@app.route("/comments/<int:comment_id>/edit", methods=["PATCH"])
@require_auth
def edit_comment(comment_id):
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    data = request.json or {}
    body = data.get("body", "").strip()
    if not body:
        return jsonify({"error": "Comment body required"}), 400

    try:
        comment = supabase.table("comments").select("username").eq("id", comment_id).single().execute()
    except Exception:
        return jsonify({"error": "Comment not found"}), 404

    if comment.data["username"].lower() != username.lower():
        return jsonify({"error": "You can only edit your own comments"}), 403

    supabase.table("comments").update({
        "body":      body,
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", comment_id).execute()
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

    if target_type == "hr_event":
        try:
            event = supabase.table("hr_events").select("drinker, drink_type").eq("id", target_id).single().execute().data
            dl_res = supabase.table("drink_log").select("given_to").eq("hr_event_id", target_id).execute()
            dl = dl_res.data[0] if dl_res.data else None
            assignee = dl.get("given_to") if dl else None

            if event["drink_type"] == "i_drink":
                send_push_to_users([event["drinker"]], "⚾ Cheers!",
                    f"{username.capitalize()} says cheers!", exclude=username,
                    data={"type": "like", "hr_event_id": target_id}, notif_type="like")
                write_notification("like", "⚾ Cheers!", f"{username.capitalize()} says cheers!",
                    {"type": "like", "hr_event_id": target_id})
            else:
                send_push_to_users([event["drinker"]], "⚾ Nice one!",
                    f"{username.capitalize()} says nice one!", exclude=username,
                    data={"type": "like", "hr_event_id": target_id}, notif_type="like")
                write_notification("like", "⚾ Nice one!", f"{username.capitalize()} says nice one!",
                    {"type": "like", "hr_event_id": target_id})
                if assignee:
                    send_push_to_users([assignee], "⚾ Bottoms up!",
                        f"{username.capitalize()} says bottoms up!", exclude=username,
                        data={"type": "like", "hr_event_id": target_id}, notif_type="like")
                    write_notification("like", "⚾ Bottoms up!", f"{username.capitalize()} says bottoms up!",
                        {"type": "like", "hr_event_id": target_id})
        except Exception as e:
            print(f"[PUSH] Like notify failed: {e}")

    return jsonify({"liked": True}), 200


# ---------------------------------------------------------------------------
# Video upload notification — personalised "your chug" vs "[name]'s chug"
# ---------------------------------------------------------------------------

@app.route("/videos/notify", methods=["POST"])
@require_auth
def notify_video_upload():
    data        = request.json
    hr_event_id = data.get("hr_event_id")
    player_name = data.get("player_name", "")
    video_id    = data.get("video_id")
    actual_drinker = data.get("actual_drinker", "")  # who the chug belongs to

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    uploader = profile_res.data["username"]

    try:
        subs = supabase.table("push_subscriptions").select("username").execute()
        all_users = [s["username"] for s in (subs.data or []) if s["username"].lower() != uploader.lower()]

        push_data = {"type": "video", "hr_event_id": hr_event_id, "video_id": video_id}

        # Build personalised bodies
        targets = []
        for u in all_users:
            if u.lower() == (actual_drinker or "").lower():
                body = f"{uploader.capitalize()} uploaded your chug for {player_name}'s homer!"
            else:
                body = f"{uploader.capitalize()} uploaded {actual_drinker.capitalize() if actual_drinker else 'a'}'s chug for {player_name}'s homer!"
            targets.append((u, body))

        send_push_targeted(targets, "🎥 New Chug Video!", data=push_data, notif_type="video")

        # Write one generic notification for the in-app feed
        write_notification("video", "🎥 New Chug Video!",
            f"{uploader.capitalize()} uploaded a chug for {player_name}'s homer!",
            push_data
        )
    except Exception as e:
        print(f"[PUSH] Video notify failed: {e}")

    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Video auto-cleanup
# ---------------------------------------------------------------------------

@app.route("/videos/cleanup", methods=["POST"])
@require_auth
def cleanup_videos():
    MAX_VIDEOS = 10
    try:
        res = supabase.table("chug_videos").select("id, storage_path").order("created_at", desc=False).execute()
        videos = res.data or []
    except Exception as e:
        print(f"[CLEANUP] Failed to fetch videos: {e}")
        return jsonify({"error": str(e)}), 500

    to_delete_count = max(0, len(videos) - (MAX_VIDEOS - 1))
    if to_delete_count == 0:
        return jsonify({"success": True, "deleted": 0}), 200

    deleted = 0
    for video in videos[:to_delete_count]:
        try:
            supabase.storage.from_("chug-videos").remove([video["storage_path"]])
        except Exception as e:
            print(f"[CLEANUP] Storage delete failed for {video['storage_path']}: {e}")
        try:
            supabase.table("chug_videos").delete().eq("id", video["id"]).execute()
            deleted += 1
            print(f"[CLEANUP] Deleted video id={video['id']}")
        except Exception as e:
            print(f"[CLEANUP] DB delete failed for id={video['id']}: {e}")

    return jsonify({"success": True, "deleted": deleted}), 200


# ---------------------------------------------------------------------------
# Video comments — with prior-commenter notifications
# ---------------------------------------------------------------------------

@app.route("/video-comments", methods=["POST"])
@require_auth
def add_video_comment():
    data              = request.json
    video_id          = data.get("video_id")
    body              = data.get("body", "").strip()
    parent_comment_id = data.get("parent_comment_id")

    if not body:
        return jsonify({"error": "Comment body required"}), 400

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        res = supabase.table("comments").insert({
            "video_id":          video_id,
            "user_id":           str(request.user.id),
            "username":          username,
            "body":              body,
            "parent_comment_id": parent_comment_id,
        }).execute()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        video = supabase.table("chug_videos").select("uploader, player_name, actual_drinker").eq("id", video_id).single().execute().data
        if not video:
            return jsonify(res.data[0]), 201

        notify_set = set()
        notify_set.add(video["uploader"].lower())
        if video.get("actual_drinker"):
            notify_set.add(video["actual_drinker"].lower())

        if parent_comment_id:
            try:
                parent_res = supabase.table("comments").select("username").eq("id", parent_comment_id).single().execute()
                notify_set.add(parent_res.data["username"].lower())
            except Exception:
                pass
        else:
            prior = get_prior_commenters(video_id=video_id, exclude=username)
            notify_set.update(prior)

        notify_set.discard(username.lower())

        # Personalise "your chug" for the actual drinker
        push_data = {"type": "comment", "video_id": video_id}
        targets = []
        notif_body_generic = f"{username.capitalize()} commented on {video['actual_drinker'].capitalize() if video.get('actual_drinker') else 'a'}'s chug for {video['player_name']}'s homer!"
        for u in notify_set:
            if u == (video.get("actual_drinker") or "").lower():
                body_msg = f"{username.capitalize()} commented on your chug for {video['player_name']}'s homer!"
            else:
                body_msg = notif_body_generic
            targets.append((u, body_msg))

        send_push_targeted(targets, "💬 New Comment", data=push_data, notif_type="comment")
        write_notification("comment", "💬 New Comment", notif_body_generic, push_data)
    except Exception as e:
        print(f"[PUSH] Video comment notify failed: {e}")

    return jsonify(res.data[0]), 201


@app.route("/video-comments/<int:comment_id>", methods=["DELETE"])
@require_auth
def delete_video_comment(comment_id):
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        comment = supabase.table("comments").select("username").eq("id", comment_id).single().execute()
    except Exception:
        return jsonify({"error": "Comment not found"}), 404

    if comment.data["username"].lower() != username.lower():
        return jsonify({"error": "You can only delete your own comments"}), 403

    supabase.table("comments").delete().eq("id", comment_id).execute()
    return jsonify({"success": True}), 200


@app.route("/video-comments/<int:comment_id>/edit", methods=["PATCH"])
@require_auth
def edit_video_comment(comment_id):
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    data = request.json or {}
    body = data.get("body", "").strip()
    if not body:
        return jsonify({"error": "Comment body required"}), 400

    try:
        comment = supabase.table("comments").select("username").eq("id", comment_id).single().execute()
    except Exception:
        return jsonify({"error": "Comment not found"}), 404

    if comment.data["username"].lower() != username.lower():
        return jsonify({"error": "You can only edit your own comments"}), 403

    supabase.table("comments").update({
        "body":      body,
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", comment_id).execute()
    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Video likes — personalised "your chug" body
# ---------------------------------------------------------------------------

@app.route("/video-likes", methods=["POST"])
@require_auth
def toggle_video_like():
    data     = request.json
    video_id = data.get("video_id")

    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    existing = supabase.table("likes").select("id").eq("user_id", str(request.user.id)).eq("target_type", "chug_video").eq("target_id", video_id).execute()

    if existing.data:
        supabase.table("likes").delete().eq("id", existing.data[0]["id"]).execute()
        return jsonify({"liked": False}), 200

    supabase.table("likes").insert({
        "user_id":     str(request.user.id),
        "username":    username,
        "target_type": "chug_video",
        "target_id":   video_id,
    }).execute()

    try:
        video = supabase.table("chug_videos").select("uploader, player_name, actual_drinker").eq("id", video_id).single().execute().data
        if video and video["uploader"].lower() != username.lower():
            actual = (video.get("actual_drinker") or video["uploader"]).lower()
            push_data = {"type": "like", "video_id": video_id}

            # Send to uploader and actual drinker (deduplicated)
            targets = []
            notified = set()
            for recipient in [video["uploader"], video.get("actual_drinker")]:
                if not recipient or recipient.lower() == username.lower():
                    continue
                r = recipient.lower()
                if r in notified:
                    continue
                notified.add(r)
                if r == actual:
                    body_msg = f"{username.capitalize()} liked your chug for {video['player_name']}'s homer!"
                else:
                    body_msg = f"{username.capitalize()} liked {actual.capitalize()}'s chug for {video['player_name']}'s homer!"
                targets.append((recipient, body_msg))

            send_push_targeted(targets, "🍺 Cheers!", data=push_data, notif_type="like")
            write_notification("like", "🍺 Cheers!",
                f"{username.capitalize()} liked a chug for {video['player_name']}'s homer!",
                push_data
            )
    except Exception as e:
        print(f"[PUSH] Video like notify failed: {e}")

    return jsonify({"liked": True}), 200


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@app.route("/notifications", methods=["GET"])
@require_auth
def get_notifications():
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    try:
        notifs = supabase.table("notifications").select("*").order("created_at", desc=True).limit(20).execute()
        if not notifs.data:
            return jsonify([]), 200

        ids = [n["id"] for n in notifs.data]
        reads = supabase.table("notification_reads").select("notification_id").eq("username", username).in_("notification_id", ids).execute()
        read_ids = {r["notification_id"] for r in (reads.data or [])}

        result = [{**n, "read": n["id"] in read_ids} for n in notifs.data]
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notifications/read", methods=["POST"])
@require_auth
def mark_notifications_read():
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    data = request.json or {}
    ids  = data.get("notification_ids")

    try:
        if not ids:
            notifs = supabase.table("notifications").select("id").order("created_at", desc=True).limit(20).execute()
            ids = [n["id"] for n in (notifs.data or [])]
        if not ids:
            return jsonify({"success": True}), 200
        rows = [{"username": username, "notification_id": nid} for nid in ids]
        supabase.table("notification_reads").upsert(rows, on_conflict="username,notification_id").execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Push notification preferences
# ---------------------------------------------------------------------------

@app.route("/notifications/preferences", methods=["GET"])
@require_auth
def get_push_preferences():
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    all_types = ["hr", "assignment", "approval", "late", "comment", "like", "video"]
    try:
        res = supabase.table("notification_preferences").select("type, enabled").eq("username", username.lower()).execute()
        saved = {r["type"]: r["enabled"] for r in (res.data or [])}
        prefs = {t: saved.get(t, True) for t in all_types}
        return jsonify(prefs), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notifications/preferences", methods=["POST"])
@require_auth
def set_push_preferences():
    profile_res = supabase.table("profiles").select("username").eq("id", request.user.id).single().execute()
    username = profile_res.data["username"]

    data = request.json or {}
    all_types = ["hr", "assignment", "approval", "late", "comment", "like", "video"]

    try:
        rows = [
            {"username": username.lower(), "type": t, "enabled": bool(data.get(t, True))}
            for t in all_types if t in data
        ]
        if rows:
            supabase.table("notification_preferences").upsert(rows, on_conflict="username,type").execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        # Remove any existing subscriptions for this user that have a different endpoint
        # (handles iOS silently rotating the push endpoint)
        supabase.table("push_subscriptions").delete().eq("user_id", str(request.user.id)).neq("endpoint", endpoint).execute()

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
    refresh_late_statuses(notify=False)
    return jsonify({"status": "ok", "app": "Going Yard & Drinking Hard"}), 200


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
