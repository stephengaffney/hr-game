# Going Yard & Drinking Hard — Backend

**The Flask API** powering [Going Yard & Drinking Hard](https://going-yard-frontend.vercel.app/), an MLB home run drinking game tracker built for a group of friends during the 2026 season.

**Live backend:** `https://hr-game-production-140c.up.railway.app`  
**Frontend repo:** [stephengaffney/going-yard-frontend](https://github.com/stephengaffney/going-yard-frontend)

---

## What This Is

Going Yard & Drinking Hard is a real-time drinking game tied to live MLB home run data. Seven players each own two MLB players — when one hits a homer, the player-owner either has to drink themselves ("I Drink") or assign a drink to someone else ("You Drink"). Everything is tracked in real time: drink status, 24-hour timers, approvals, a leaderboard, and optional chug video uploads.

This repo contains:
- **`app.py`** — The Flask REST API deployed on Railway
- **`hr_poller4.py`** — A polling script that runs locally on a Mac desktop 24/7, watching the MLB Stats API and firing the webhook whenever a home run is detected

For full game mechanics, see [GAME_MECHANICS.md](./GAME_MECHANICS.md).

---

## Architecture

```
MLB Stats API (statsapi.mlb.com)
      │
      │  polled every 60 seconds
      ▼
hr_poller4.py                    ← Mac desktop, runs 24/7
      │
      │  POST /webhook/hr
      ▼
app.py on Railway                ← Flask API, always-on PaaS
      │
      ├── Supabase Postgres       ← inserts hr_events + drink_log rows
      └── Web Push (VAPID)        ← pushes to all subscribed devices
                 │
                 ▼
         User devices             ← iOS / Android / desktop browsers

Frontend (Vercel) ──────────────► Supabase Realtime
                                   (open WebSocket, receives row changes instantly)
```

The frontend reads directly from Supabase via the anon key and receives live updates via Supabase Realtime. The Flask backend handles anything requiring the service role key or shared secrets: webhook intake, drink assignments, approvals, push notifications, and video upload coordination.

---

## Services Used

### Railway
Railway is a Platform-as-a-Service (PaaS) that hosts and runs `app.py`. It connects directly to the GitHub repo — every push to the main branch triggers an automatic redeploy. Railway builds the app using Nixpacks (no Dockerfile needed, configured via `railway.json`) and runs it with the command in `Procfile`:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60
```

Railway injects `PORT` automatically. All other secrets (Supabase keys, VAPID keys, webhook secret) are set as environment variables in the Railway dashboard and read by `app.py` via `os.environ.get()`.

Railway's uptime monitoring pings `/health` on a regular schedule. This is deliberately used as a passive cron job — every health ping triggers the late status sweep and orphan heal without needing a separate scheduler.

### Supabase
Supabase provides four distinct services that this app uses simultaneously:

| Supabase service | How it's used |
|---|---|
| **Postgres database** | All game state — events, drink log, assignments, profiles, notifications, preferences |
| **Auth** | User sign-up and sign-in (email + password). JWTs issued by Supabase Auth are validated by the Flask backend on every protected route. |
| **Realtime** | The frontend subscribes to Postgres row changes via WebSocket. Every `HRCard` listens for changes to its own `drink_log`, `drink_assignments`, `comments`, and `likes` rows in real time. |
| **Storage** | Chug videos are uploaded directly from the browser into the `chug-videos` bucket. Public URLs are stored in the `chug_videos` table and served directly to other users — the Flask backend is not involved in the upload itself. |

The Flask backend uses the **service role key**, which bypasses Row Level Security and has full read/write access. The frontend uses the **anon key**, which is safe to expose publicly and is limited by RLS policies.

### MLB Stats API
The MLB Stats API (`https://statsapi.mlb.com/api/v1`) is a public API with no authentication required. The poller hits one endpoint per player per cycle:

```
GET https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&group=hitting&season={year}
```

The response includes a `splits` array. The poller reads `splits[0].stat.homeRuns` to get the player's cumulative season HR total. Tracking cumulative totals rather than individual game logs means:

- No need to track which games have been processed
- Delta between old and new total automatically captures multi-HR games
- The poller is stateless beyond its local `last_results.json` file

---

## Repository Structure

```
hr-game/
├── app.py                  # Flask API — all routes and business logic
├── hr_poller4.py           # Local polling script — detects HRs, fires webhook
├── requirements.txt        # Python dependencies
├── Procfile                # Railway start command (gunicorn)
├── railway.json            # Railway build config (Nixpacks builder)
├── manifest.json           # PWA manifest (served as a static file)
├── sw.js                   # Service worker (served as a static file)
└── generate_vapid_keys.py  # One-time utility to generate VAPID key pair
```

### Recommended `.gitignore`

```
last_results.json
game_log.json
__pycache__/
*.pyc
.env
```

`last_results.json` and `game_log.json` are live poller state files that should never be committed.

---

## The HR Poller

`hr_poller4.py` is the heartbeat of the app. It is the only thing that triggers new game events.

### How it works

1. Every 60 seconds it fetches the current season HR total for all 14 tracked players from the MLB Stats API
2. It compares each result against `last_results.json`
3. If a player's count has increased, `notify_hr()` fires, which:
   - Appends an entry to `game_log.json`
   - Sends an email alert via Gmail SMTP (only to the admin for troubleshooting purposes in case of any error)
   - POSTs to `/webhook/hr` on Railway

### Restart behavior

When the poller restarts with no saved history for a player:
- It checks `game_log.json` for the last logged HR count for that player
- If the current MLB total is higher and the last log entry was **today**, it fires a "missed HR" notification
- If the last log entry was a previous day, it silently updates the baseline without notifying — this prevents stale drink entries with wrong timestamps when the machine wakes up after an overnight gap

### Running it

```bash
# Set email credentials as environment variables first
export EMAIL_USER="your@gmail.com"
export EMAIL_PASS="your-gmail-app-password"

python hr_poller4.py
```

The poller runs indefinitely locally on Mac desktop via a plist file (caffeinated). If the machine loses power or wifi, it must be restarted manually.

### Local persistence files

| File | Purpose |
|---|---|
| `last_results.json` | Last known HR count per player, keyed by player last name |
| `game_log.json` | Full append-only log of every HR event detected |

> **Note:** The database has a `poller_state` table that was created as a potential Supabase-side mirror of `last_results.json`, intended for use if the poller was ever migrated off the local machine. It was never wired up and is not used.

---

## End-to-End Event Flow

This is the complete journey from a home run being hit to every user seeing it in their app:

1. **MLB Stats API** finalizes the game data and updates the player's season HR total
2. **`hr_poller4.py`** (Mac desktop) polls the API, detects the increase, and calls `notify_hr()`
3. **`notify_hr()`** appends to `game_log.json`, sends an email, and POSTs to `/webhook/hr` on Railway
4. **`/webhook/hr`** (Flask on Railway) validates the webhook secret, inserts a row into `hr_events` and a row into `drink_log` in Supabase Postgres
5. **Supabase Realtime** detects the new `hr_events` insert and pushes the change to all open browser sessions via WebSocket — the Feed tab's unread dot appears instantly
6. **`/webhook/hr`** calls `send_push_to_all()`, which reads all push subscriptions from the database and sends a VAPID Web Push to every subscribed device
7. **Service worker** (`sw.js`) on each device receives the push, shows a system notification with the player name and drink instruction
8. **User taps the notification** — the service worker reads the `hr_event_id` from the payload and opens the app to `/?event={id}`
9. **App loads**, reads the deep link param, switches to the Feed tab, scrolls to the matching `HRCard`, and triggers a gold flash animation
10. **`HRCard`** loads its own `drink_log`, `likes`, and `comments` from Supabase and renders the live countdown timer

---

## The Flask API

### Routes

#### Webhook (called by the poller)

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/webhook/hr` | `X-Webhook-Secret` header | Receives a new HR event. Inserts into `hr_events` and `drink_log`, sends push to all users. |

#### Drink lifecycle

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/assign` | Bearer token | Assigns a "you drink" to a specific player. Only the matched game owner can call this. Updates `drink_log.given_to` and creates a `drink_assignments` row. |
| `POST` | `/drinks/approve` | Bearer token | Marks a drink as completed. Any user except the actual drinker can approve. Computes `completed` vs `completed_late` based on the 24-hour clock. |
| `POST` | `/drinks/refresh-late` | `X-Webhook-Secret` header | Manually triggers the late status sweep. |

#### Comments

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/comments` | Bearer token | Add a top-level or reply comment to an HR event feed card. |
| `DELETE` | `/comments/<id>` | Bearer token | Delete your own comment. |
| `PATCH` | `/comments/<id>/edit` | Bearer token | Edit your own comment body. |

#### Video comments

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/video-comments` | Bearer token | Add a comment to a chug video. |
| `DELETE` | `/video-comments/<id>` | Bearer token | Delete your own video comment. |
| `PATCH` | `/video-comments/<id>/edit` | Bearer token | Edit your own video comment body. |

#### Likes

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/likes` | Bearer token | Toggle a like on an HR event (`target_type = 'hr_event'`). Sends a personalised push to the drinker. |
| `POST` | `/video-likes` | Bearer token | Toggle a like on a chug video (`target_type = 'chug_video'`). |

#### Videos

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/videos/notify` | Bearer token | Sends personalised push notifications after a chug video is uploaded. Called by the frontend after the Supabase Storage upload completes. |
| `POST` | `/videos/cleanup` | Bearer token | Deletes the oldest videos when the library exceeds 10 entries. |

#### Notifications

| Method | Route | Auth | Description |
|---|---|---|---|
| `GET` | `/notifications` | Bearer token | Fetches the 20 most recent notifications with read/unread status for the current user. |
| `POST` | `/notifications/read` | Bearer token | Marks notification IDs as read for the current user. |
| `GET` | `/notifications/preferences` | Bearer token | Gets the current user's push notification type preferences. |
| `POST` | `/notifications/preferences` | Bearer token | Saves push notification type preferences. |

#### Push subscriptions

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/push/subscribe` | Bearer token | Registers or updates a Web Push subscription. Deletes old endpoints for the same user to handle iOS endpoint rotation. |
| `GET` | `/push/vapid-public-key` | None | Returns the VAPID public key for the frontend to use when subscribing. |

#### Health

| Method | Route | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Returns `{"status": "ok"}`. Triggers the late status sweep and orphan heal on every call. Railway uptime pings drive this as a passive cron. |

---

## Business Logic

### Late status sweep

`refresh_late_statuses()` runs on every `/webhook/hr` event and every `/health` ping. It scans all `drink_log` rows with status `pending` or `awaiting_approval` from the last 7 days, checks whether they have exceeded 24 hours, and marks them `late`.

The clock start point depends on status and drink type:

| Situation | Clock starts at |
|---|---|
| `awaiting_approval` with `assigned_at` set | `assigned_at` |
| `pending` with `given_to` and `assigned_at` set | `assigned_at` |
| All other cases | `hr_triggered_at` |
| Fallback if no timestamp exists | Noon on `event_date` |

Late push notifications are only sent for drinks that crossed the threshold **today**, to avoid spamming old stale entries on restart.

### Orphan heal

`heal_orphaned_events()` runs on every `/health` ping. It finds any `hr_events` rows from the last 30 days that have no matching `drink_log` row and inserts the missing entry. This handles the edge case where `/webhook/hr` successfully inserted `hr_events` but then failed to insert `drink_log`. Without a `drink_log` row the feed card has no approve button and cannot be completed.

### Assignment rollback

The `/assign` route updates `drink_log` before inserting into `drink_assignments`. If the `drink_assignments` insert fails, the route rolls back the `drink_log` update so the assigner can safely retry.

### Push notification helpers

| Function | Use case |
|---|---|
| `send_push_to_all()` | Broadcasts to all subscribed users. Used for HR events and late alerts. |
| `send_push_to_users()` | Same body to a specific list of users. Used for assignment and approval notifications. |
| `send_push_targeted()` | Personalised bodies per user. Used for video uploads ("your chug" vs "[name]'s chug") and video likes. |

All three respect per-user notification preferences via a single bulk query per call.

---

## Database (Supabase)

### Tables

| Table | Type | Purpose |
|---|---|---|
| `hr_events` | Table | One row per detected home run. Permanent record and source of truth for the feed. |
| `drink_log` | Table | One row per HR event. Tracks drink status, ownership, assignment, approval, and all timestamps. |
| `drink_assignments` | Table | Created when a `you_drink` is assigned. Stores assigner, assignee, message. One row per HR event (UNIQUE on `hr_event_id`). |
| `profiles` | Table | User accounts linked to Supabase Auth. Stores username, display name, color. |
| `push_subscriptions` | Table | Web Push endpoint, p256dh key, and auth key per device. Endpoint is unique. |
| `notification_preferences` | Table | Per-user boolean toggle for each notification type. Unique index on `(username, type)`. |
| `notifications` | Table | Global notification log for the in-app bell dropdown. Read state tracked separately. |
| `notification_reads` | Table | Which notifications each user has read. Unique index on `(username, notification_id)` backs the upsert in `/notifications/read`. |
| `comments` | Table | Comments on feed cards and chug videos. `hr_event_id` or `video_id` set per row (not both). Self-referential `parent_comment_id` for threading. |
| `likes` | Table | Likes on HR events (`target_type = 'hr_event'`) and videos (`target_type = 'chug_video'`). |
| `chug_videos` | Table | Metadata for uploaded videos. Files live in Supabase Storage `chug-videos` bucket. |
| `app_settings` | Table | Key-value feature flags. Currently: `videos_enabled`. |
| `hr_totals` | **View** | Live view over `hr_events`. `MAX(new_hrs)` per player. Always current, no writes needed. |
| `leaderboard` | **View** | Live view over `drink_log`. Counts per `username` split by drink type. Counts obligation owner, not physical drinker. |
| `poller_state` | Table | Unused. Created for a planned poller migration that never happened. |
| `reactions` | Table | Unused. Created for an emoji reaction feature that was never completed. |

### View definitions

**`hr_totals`**
```sql
SELECT player_key, full_name, team, MAX(new_hrs) AS current_hrs
FROM hr_events
GROUP BY player_key, full_name, team
ORDER BY MAX(new_hrs) DESC;
```

**`leaderboard`**
```sql
SELECT
  username,
  COUNT(*) FILTER (WHERE drink_type = 'i_drink') AS drinks_received,
  COUNT(*) FILTER (WHERE drink_type = 'you_drink') AS drinks_assigned,
  COUNT(*) AS total_drinks
FROM drink_log
GROUP BY username;
```

### `drink_log` status lifecycle

```
HR detected → drink_log created (status = 'pending')
      │
      ├─ (i_drink) ──────────────────────────────────► awaiting_approval
      │                                                        │
      └─ (you_drink, assigned via /assign) ───────────► awaiting_approval
                                                               │
      (24h elapsed) ─────────────────────────────────────► late
                                                               │
      (approved by anyone except the drinker) ────────► completed
                                                               │
              (approved after 24h elapsed) ─────────► completed_late

      (you_drink, never assigned, 24h elapsed) ───────► late*
```

> *`vacated` is frontend-only. Any `late` row where `drink_type = 'you_drink'` and `given_to` is null is displayed as "Vacated" in the UI. The database only stores `late`.

### `drink_log` schema notes

- `assigned_at` defaults to `now()` at row creation, not strictly at assignment time. It only has clock significance when `given_to` is also set.
- `status` CHECK constraint allows: `pending`, `awaiting_approval`, `completed`, `late`, `completed_late` only.

---

## Environment Variables (Railway)

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (bypasses RLS) |
| `VAPID_PRIVATE_KEY` | VAPID private key for Web Push |
| `VAPID_PUBLIC_KEY` | VAPID public key (returned to frontend via `/push/vapid-public-key`) |
| `VAPID_EMAIL` | Contact email embedded in VAPID claims |
| `WEBHOOK_SECRET` | Shared secret validated on `/webhook/hr` and `/drinks/refresh-late` |
| `PORT` | Injected automatically by Railway |

---

## Player & Matchup Reference

| Participant | I Drink Player | You Drink Player |
|---|---|---|
| Frank | Yanier Diaz | Yordan Alvarez |
| Scott | Adolis Garcia | Bryce Harper |
| Tyler | Anthony Volpe | Ben Rice |
| Ned | Jasson Dominguez | Jazz Chisholm Jr. |
| Ryan | Trea Turner | Kyle Schwarber |
| Steve | Austin Wells | Trent Grisham |
| Dan | Ryan McMahon | Aaron Judge |

---

## Generating VAPID Keys

Generated once using `generate_vapid_keys.py`. Only needs to be rerun if setting up the push system from scratch.

```bash
pip install pywebpush
python generate_vapid_keys.py
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | Flask 3.0 |
| WSGI server | Gunicorn 22 |
| Hosting | Railway (Nixpacks, auto-deploy from GitHub) |
| Database | Supabase Postgres |
| Auth | Supabase Auth (JWT, email + password) |
| Realtime | Supabase Realtime (WebSocket, Postgres changes) |
| Storage | Supabase Storage (`chug-videos` bucket) |
| Push notifications | Web Push / VAPID via `pywebpush` 2.0 |
| MLB data source | MLB Stats API (public, no auth required) |
| Email alerts | Gmail SMTP (poller only) |
