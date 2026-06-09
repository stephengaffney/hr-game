# Going Yard & Drinking Hard — Game Mechanics

This document explains how the game works for anyone reading the codebase who wants to understand what's being tracked, why certain decisions were made, and how edge cases are handled.

---

## The Premise

Seven friends each "own" two MLB players for a season. Whenever one of those players hits a home run during a real MLB game, the game owner has a drinking obligation — either drinking themselves or assigning the drink to someone else. Every obligation has a 24-hour window to be fulfilled.

The app tracks all of this in real time using live MLB stats data, push notifications, and a shared feed of timestamped events that the whole group can comment on and react to.

---

## The Roster

Seven participants each own two MLB players — one from each of two categories:

- **"I Drink" player** — when this player hits a homer, the owner must drink
- **"You Drink" player** — when this player hits a homer, the owner must assign the drink to someone else in the group

| Participant | I Drink | You Drink |
|---|---|---|
| Frank | Yanier Diaz | Yordan Alvarez |
| Scott | Adolis Garcia | Bryce Harper |
| Tyler | Anthony Volpe | Ben Rice |
| Ned | Jasson Dominguez | Jazz Chisholm Jr. |
| Ryan | Trea Turner | Kyle Schwarber |
| Steve | Austin Wells | Trent Grisham |
| Dan | Ryan McMahon | Aaron Judge |

The roster is fixed for the season. There is no mid-season trading or substitution.

---

## How a Drink Is Triggered

### Detection

The HR poller (`hr_poller4.py`) runs on a Mac desktop 24/7 and polls the MLB Stats API every 60 seconds. It calls this endpoint for each of the 14 tracked players:

```
GET https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&group=hitting&season={year}
```

It reads `splits[0].stat.homeRuns` from the response — the player's **cumulative season HR total**, not individual game logs. When a player's total increases, a home run has been hit.

Tracking cumulative totals means:
- No need to process individual game records
- If a player hits 2 HRs in a single game and the poller catches both in one cycle, the delta (`new_hrs - old_hrs`) correctly reflects the count — "Frank must drink 2 beers" (Should only happen during downtime considering the polling cycle is once per minute)
- Stats inherit the MLB API's timing, typically within a few minutes of the game event.

### What gets created

When a new HR is detected, the backend script creates two database rows:

1. **`hr_events`** — the permanent record: player info, old and new HR counts, which game participant is affected, drink type (`i_drink` or `you_drink`), and a randomly chosen announcer slogan
2. **`drink_log`** — the mutable tracking record that changes status as the drink progresses through its lifecycle

---

## Drink Types

### "I Drink"

The player-owner must drink a beer themselves.

1. HR detected → all players receive a push notification
2. Owner drinks the beer within 24 hours
3. Anyone else in the group who witnesses it taps **"I Saw Them Drink"** on the feed card
4. Drink is marked `completed`

The 24-hour clock starts when the HR is detected.

### "You Drink"

The game owner must assign the drink to someone else in the group.

1. HR detected → all players receive a push notification
2. Owner opens the app, taps "Assign Drink", selects a player, and optionally adds a message (e.g. "payback for last week") within 24 hours of the HR event
3. The assignee receives a personalised push notification: "You've been assigned a drink"
4. The assignee must drink within 24 hours **of being assigned**
5. Anyone who witnesses it taps **"I Saw Them Drink"**
6. Drink is marked `completed`

The 24-hour clock for the assignee starts **when the assignment is made**, not when the HR was hit. This gives the owner reasonable time to open the app and choose who to assign before the countdown begins for the actual drinker.

---

## The 24-Hour Clock

Every drink has a 24-hour window. The countdown is displayed live on every active feed card, updating every second.

The clock color changes as the deadline approaches:
- **Green** — more than 6 hours remaining
- **Gold** — less than 6 hours remaining
- **Red pulsing** — less than 2 hours remaining

Clock start point by drink type and state:

| Situation | Clock starts at |
|---|---|
| `i_drink` | HR detection time (`hr_triggered_at`) |
| `you_drink`, not yet assigned | HR detection time (`hr_triggered_at`) |
| `you_drink`, assigned | Assignment time (`assigned_at`) |

---

## Drink Status Lifecycle

```
HR detected
     │
     ▼
  pending
     │
     ├─ (i_drink) ──────────────────────────────────────► awaiting_approval
     │                                                           │
     └─ (you_drink: owner assigns via app) ──────────────► awaiting_approval
                                                                 │
     (24h elapsed from clock start) ─────────────────────► late
                                                                 │
     (witnessed and confirmed by anyone except drinker) ────► completed
                                                                 │
                         (if 24h already elapsed at approval) ► completed_late

     (you_drink: never assigned within 24h) ──────────────► late [displayed as vacated]*
```

> *A `you_drink` that expired without ever being assigned is stored as `late` in the database. The frontend displays it as **Vacated** for any `late` row where `drink_type = 'you_drink'` and `given_to` is null.

### Status reference

| Status | In DB | Meaning |
|---|---|---|
| `pending` | ✅ | Drink triggered but not yet fulfilled or assigned |
| `awaiting_approval` | ✅ | Assigned (you_drink) or waiting for a witness (i_drink) |
| `late` | ✅ | 24-hour window elapsed without completion |
| `completed` | ✅ | Confirmed within the 24-hour window |
| `completed_late` | ✅ | Eventually confirmed, but after the deadline |
| `vacated` | ❌ frontend only | A `late` you_drink that was never assigned — obligation expired with no drinker named |

---

## Approval

Anyone in the group — except the person who is supposed to drink — can confirm a drink was consumed by tapping **"I Saw Them Drink"** on the feed card.

This is deliberately an honor system with social accountability. The whole group can see the feed, comment, and call people out. There is no formal dispute mechanism. Occasional occurences of a drink being completed on time and the approval being late will be manually updated via the database. --Maybe in the future we will add a mechanic to say "I Saw Them Drink" at the time in which the video was uplaoded?

If a drink is approved after the 24-hour window has already elapsed, it is marked `completed_late` rather than `completed`. The leaderboard tracks late completions separately as a badge on each player's entry.

---

## Notifications

The app sends push notifications for every significant game event. Each user can toggle individual types on or off in Settings.

| Notification | Who receives it |
|---|---|
| HR alert | Everyone |
| Drink assigned | Everyone (personalised: assignee sees "you've been assigned", others see "[name] was assigned") |
| Drink confirmed | Everyone except the approver |
| Late drink alert | Everyone |
| Comment | The drinker, assignee, and any prior commenters on that card |
| Like | The drinker (and assignee if applicable) |
| Chug video uploaded | Everyone (personalised: drinker sees "your chug", others see "[name]'s chug") |

Push notifications deep-link directly into the app. Tapping one opens the app and scrolls to the specific feed card or video, with a brief gold flash animation on the target card.

---

## The Leaderboard

### Drink Leaderboard
Ranks all seven players by total drinks consumed. Uses a podium layout — 1st, 2nd, and 3rd on raised platforms, the rest in a flat list below. Late completion counts and vacated drink counts are shown as secondary badges on each entry. This is the primary competitive view.

### 20 HR Slugger
A progress bar race showing how close each player's "I Drink" MLB player is to hitting 20 home runs on the season. Tracks who is on target to reach the 20HR goal.

### Big Hitter
A bar chart of how many HRs each player's "You Drink" MLB player has accumulated. A high count here means that owner has had to assign a lot of drinks to others.

### MLB Home Runs
All 14 tracked players ranked by current season HR total. Pure baseball — no game mechanics attached.

### Combined
Powered by a Supabase SQL view that aggregates `drink_log` rows by `username`. This counts by obligation owner, not physical drinker — if Frank assigns a drink to Scott, it counts toward Frank's `drinks_assigned` total, not Scott's `drinks_received`.

---

## Chug Videos

Players can record or upload a short video of themselves drinking directly in the app. This feature is admin-toggled and off by default; and can be disabled on enabled directly in the app if the user has permissions. This feature is mostly to ensure users do not drink without proof if there is a known issue with the app videos and the admin is not able to fix it immediately.

Key constraints:
- Maximum 25 seconds per video
- Maximum 10 videos stored across the whole group at any time — oldest is automatically deleted when a new one is uploaded
- Videos upload directly from the browser to Supabase Storage (the Flask backend is not involved in the upload)
- In-app recording uses the browser's `MediaRecorder` API, supported on iOS Safari and modern Android Chrome
- Each video is linked to the specific HR event that triggered the drink

Videos appear in the Chugs tab where others can like and comment. Each video card includes a link back to the originating feed card.

---

## Feed Cards

Each HR event produces a card in the Feed tab. A card displays:

- MLB player headshot (from `img.mlbstatic.com`) with a colored fallback if the image fails
- Player name, team logo, and HR count delta (e.g. "12 → 13 HR")
- A randomly selected baseball announcer slogan (e.g. "He tattooed that baseball!")
- The drink obligation line — who drinks, and for which player's homer
- Current drink status badge
- Live countdown timer, updating every second while the drink is active
- Timestamp — relative ("3h ago") for recent events, absolute for events older than 24 hours
- Action buttons contextual to the current user: Like, Comment, Assign Drink, I Saw Them Drink, Record Chug, View Chug
- Threaded comments with reply support and edit/delete for your own comments

Card borders animate to signal urgency: gold pulsing for active pending or awaiting drinks, red pulsing for late, static orange for late completions. New cards slide up into the feed as they arrive.

---

## Edge Cases & Design Decisions

**What if the poller misses a HR while the Mac is sleeping?**
On restart, the poller compares the current MLB HR total against the last entry in `game_log.json`. If there's a gap and the last log entry was from today, it fires a "missed HR" notification with the correct delta. If the gap spans overnight, it silently updates the baseline without notifying — this prevents stale drink entries with wrong timestamps the morning after a game day.

**What if the backend creates `hr_events` but fails to create `drink_log`?**
A self-healing function (`heal_orphaned_events`) runs on every `/health` ping. It finds any `hr_events` from the last 30 days with no matching `drink_log` row and inserts the missing entry. Without a `drink_log` row the feed card has no approve button and can never be completed. Railway's uptime monitoring pings `/health` regularly, making this a passive cron job with no separate scheduler needed.

**What if a player hits 2 HRs in one polling cycle?**
The delta between old and new HR totals covers all HRs in the gap. One drink obligation is created for the full count — the notification reads "Frank must drink 2 beers" and a single `drink_log` row tracks the obligation.

**What if Supabase replication lags after a drink assignment?**
After a `drink_assignments` Realtime event arrives, the frontend retries loading the `drink_log` row with exponential backoff (1.5s, 2.5s, 4s, 6s) to handle the case where `given_to` is still null due to replication delay before re-rendering the card.

**What if an iOS push endpoint rotates silently?**
On every app load, the frontend compares the VAPID key in the existing push subscription against the current key. If they differ, it unsubscribes and re-subscribes with a fresh endpoint. The backend also deletes stale endpoints for the same user when a new one is registered, preventing duplicate deliveries.

**What if the `/assign` backend call partially fails?**
The `/assign` route updates `drink_log` before inserting into `drink_assignments`. If the `drink_assignments` insert fails after `drink_log` has already been updated, the route rolls `drink_log` back to its previous state so the assigner can safely retry without being blocked by the "drink already assigned" duplicate guard.

**What if a player has no official MLB headshot?**
The `PlayerHeadshot` component catches image load errors and renders a colored circle with the player's initials instead, using the player's assigned color from the `PLAYER_COLORS` constant.
