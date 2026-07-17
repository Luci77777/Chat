# Pingback — a friends-only messaging site (Django)

A small messaging app where people can:

- Sign up with a username
- Search for other users by username
- Send / accept / decline friend requests
- Message **only** people who are their friends (enforced both in the UI and on the server — non-friends get a `403` if they try)
- Chat with auto-refresh (polls for new messages every 3 seconds — no WebSocket server required, so it runs on almost any free host)
- Sidebar badges for new friend requests / unread messages update live on **any** page via a lightweight background poll — see "How the live updates work" below
- Send emoji (built-in, no API/key needed), and search + send GIFs or stickers (powered by the free [Klipy](https://klipy.com) API — see "GIFs & stickers" below)
- Voice and video calls between friends, using WebRTC — media goes directly between the two browsers (peer-to-peer), so it doesn't touch or slow down your server

Built with plain Django + SQLite/Postgres + vanilla JS. No frontend framework needed.

## Project layout

```
core/          # Django project settings, root urls
accounts/      # custom User model, signup/login/profile
friends/       # friend requests + friendships + username search
chat/          # messages, inbox, chat room, poll/send endpoints, GIF/sticker search proxy
calls/         # voice/video call signaling (WebRTC offer/answer exchange, TURN credentials)
templates/     # all HTML templates
static/css/    # the one stylesheet
```

## 1. Run it locally

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then open .env and set values if you want

python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver
```

Visit `http://127.0.0.1:8000/accounts/signup/`, create a couple of accounts (use a different browser or an incognito window for the second one), search for each other's usernames, send/accept a friend request, and start chatting.

## 2. Deploy it for free

Below are two solid free options. **Render is the easier / recommended one** because it gives you a real (free) Postgres database, so your data survives redeploys. PythonAnywhere is a good fallback if you'd rather not use a database add-on.

### Option A — Render.com (recommended)

Render's free web-service tier spins down after ~15 minutes of no traffic and takes ~30–60s to wake back up on the next request — that's the trade-off for free hosting, but everything works fine once it's awake.

1. Push this project to a GitHub repository.
2. On Render.com, create a **free PostgreSQL database** first (Dashboard → New → PostgreSQL). Copy its "Internal Database URL".
3. Create a **New → Web Service**, connect your GitHub repo.
4. Set:
   - **Build command:** `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - **Start command:** `gunicorn core.wsgi:application` *(Render also auto-detects the `Procfile`, so this is optional)*
5. Add environment variables under the service's **Environment** tab:
   - `SECRET_KEY` → any long random string
   - `DEBUG` → `False`
   - `DATABASE_URL` → the Postgres URL from step 2
   - `ALLOWED_HOSTS` → `your-app-name.onrender.com` (Render also auto-adds `RENDER_EXTERNAL_HOSTNAME` for you, which `settings.py` already reads)
   - `KLIPY_API_KEY` → optional, only needed if you want the GIF/sticker button to work (see "GIFs & stickers" below)
   - `METERED_APP_DOMAIN` / `METERED_API_KEY` → optional, improves call reliability across tricky networks (see "Voice & video calls" below)
6. Deploy. Render runs the `release` step in the `Procfile` (`python manage.py migrate`) automatically on every deploy.
7. Once it's live, open a **Shell** tab on the service and run `python manage.py createsuperuser` if you want admin access.

### Option B — PythonAnywhere (no database add-on needed)

PythonAnywhere's free tier is always-on (no spin-down) but only allows outbound requests to a small allow-list and doesn't offer WebSockets — which is fine here since this app doesn't use them.

1. Create a free account at pythonanywhere.com and open a **Bash console**.
2. `git clone` your repo, then:
   ```bash
   mkvirtualenv --python=python3.11 pingback-env
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```
3. Go to the **Web** tab → **Add a new web app** → **Manual configuration** → pick the same Python version.
4. Set the **Virtualenv** path to the one you just created.
5. Edit the generated **WSGI configuration file** to point `sys.path` at your project folder and set `DJANGO_SETTINGS_MODULE = "core.settings"`.
6. In the **Static files** section, map `/static/` → `.../messenger/staticfiles` and `/media/` → `.../messenger/media`.
7. Under **Environment variables** (or by editing the WSGI file directly), set `SECRET_KEY`, `DEBUG=False`, and `ALLOWED_HOSTS=yourusername.pythonanywhere.com`.
8. Reload the web app from the **Web** tab.

> PythonAnywhere's free tier's SQLite file lives on persistent disk, so you don't need Postgres there — just don't delete `db.sqlite3`.

## GIFs & stickers

Chat's GIF/sticker search is powered by **[Klipy](https://klipy.com)**, a free GIF/sticker API (the kind of thing WhatsApp, Discord, and Figma use). It's the option that's actually free right now — Google shut down the Tenor API on June 30, 2026, and GIPHY's free tier is gone.

To turn it on:

1. Go to [klipy.com/developers](https://klipy.com/developers) and create a free account/app to get an API key.
2. Set it as the `KLIPY_API_KEY` environment variable (locally in `.env`, or in your host's environment variables settings).
3. That's it — the GIF button in chat lights up automatically once a key is present.

If you don't set a key, the app still works completely fine: the GIF/sticker button is just disabled. **Emoji don't need any of this** — they're a small built-in picker (`static/js/emoji-data.js`) with zero API calls, so they always work and never cost anything or hit a rate limit.

The Klipy key is only ever used **server-side** (`chat/klipy.py` + the `chat:gif_search` view proxy it) — it's never sent to the browser, so it can't be scraped out of your page source.

## Voice & video calls

Calling uses **WebRTC**: once two friends' browsers agree to connect, audio/video streams flow directly between them (or through a relay, see below) — never through this Django app. Our server's only job is "signaling" — a brief handshake to introduce the two browsers to each other. That handshake happens over regular HTTP polling (same pattern as the rest of the live-update system), not WebSockets, so calling works on Render's free tier — or any other free host — with no extra infrastructure (no Redis, no ASGI server, no persistent background workers).

**How a call gets connected, in short:**
1. The caller's browser records audio/video, creates a WebRTC "offer," and posts it to `/calls/start/<username>/`.
2. The callee sees it (their browser polls for incoming calls) and can Accept or Decline. Accepting posts back an "answer."
3. Once both sides have exchanged offer/answer, WebRTC connects the two browsers directly for the actual call. Our server steps out of the way at that point except for a slow "is this call still going" check every few seconds, purely to know when to end the UI on the other end if someone hangs up.

**Reliability — the one thing that costs money if you want it fully solved:** most connections between two browsers work directly (STUN, which is free — no signup, built in). But some networks (strict corporate firewalls, certain mobile carriers, roughly the toughest 10-15% of connections) need a TURN relay server to work at all. A genuinely free TURN tier still exists via **[Metered / Open Relay](https://www.metered.ca/stun-turn)** (0.5GB/month free with no card, 20GB/month free once you add one, no overage charges either way — plenty for personal use). To enable it:

1. Sign up free at [metered.ca/stun-turn](https://www.metered.ca/stun-turn) and create an app — they'll give you a subdomain like `yourapp.metered.live`.
2. Set two environment variables: `METERED_APP_DOMAIN=yourapp.metered.live` and `METERED_API_KEY=<your key>`.
3. That's it — `calls/metered.py` fetches fresh TURN credentials automatically whenever a call starts.

**Without setting this up, calling still works** for most people (STUN-only) — it just won't connect for that harder-network minority, who'll see the call ring and time out. This is a reasonable place to start for free; add TURN later if you notice failed calls.

**A couple of things worth knowing:**
- Browsers require HTTPS (or `localhost`) to access the camera/microphone — Render gives you HTTPS automatically, so this is only relevant for local testing, where `127.0.0.1`/`localhost` are already treated as secure.
- This is 1:1 calling only (matches the friends-only model) — no group calls.
- If someone has two tabs open, only one call ring/accept is tracked per pair of friends at a time (a second call attempt gets a "already in a call" response) to keep the state machine simple.
- Render's free tier spins the app down after ~15 minutes idle; the very first signaling request after a period of inactivity may take 30-60s to wake it up, same as the rest of the app.



- **Sidebar badges** (Requests / unread Chats count) poll a tiny endpoint every 12 seconds from whatever page you're on, so you'll see a new friend request or message without needing to be in that specific tab. Polling pauses automatically when the browser tab isn't visible, and backs off if requests start failing, to keep it cheap.
- **Chat rooms** poll for new messages in the open conversation every 3 seconds.
- **The inbox list** doesn't run its own separate timer — it just listens for the sidebar poll noticing your unread count changed, then refreshes the conversation list once. This avoids two polling loops doing overlapping work.



The restriction is enforced twice, not just hidden in the UI:

- `chat/views.py` checks `Friendship.are_friends(...)` in `room`, `poll_messages`, and `send_message`, and returns `403 Forbidden` (or a JSON error) if the two users aren't friends.
- The inbox and search pages simply never surface a "Message" link for a non-friend, so people don't hit that wall in normal use — it's just a safety net if someone messes with the URL directly.

## Things you may want to add later

- Rate limiting on the search/send endpoints (e.g. `django-ratelimit`) if you open this up publicly
- Email verification on signup
- File/image attachments in chat
- Push notifications (would need a background worker, which most free tiers don't include)
