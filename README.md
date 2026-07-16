# Pingback — a friends-only messaging site (Django)

A small messaging app where people can:

- Sign up with a username
- Search for other users by username
- Send / accept / decline friend requests
- Message **only** people who are their friends (enforced both in the UI and on the server — non-friends get a `403` if they try)
- Chat with auto-refresh (polls for new messages every 3 seconds — no WebSocket server required, so it runs on almost any free host)

Built with plain Django + SQLite/Postgres + vanilla JS. No frontend framework needed.

## Project layout

```
core/          # Django project settings, root urls
accounts/      # custom User model, signup/login/profile
friends/       # friend requests + friendships + username search
chat/          # messages, inbox, chat room, poll/send endpoints
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

## Notes on the "friends-only" rule

The restriction is enforced twice, not just hidden in the UI:

- `chat/views.py` checks `Friendship.are_friends(...)` in `room`, `poll_messages`, and `send_message`, and returns `403 Forbidden` (or a JSON error) if the two users aren't friends.
- The inbox and search pages simply never surface a "Message" link for a non-friend, so people don't hit that wall in normal use — it's just a safety net if someone messes with the URL directly.

## Things you may want to add later

- Rate limiting on the search/send endpoints (e.g. `django-ratelimit`) if you open this up publicly
- Email verification on signup
- File/image attachments in chat
- Push notifications (would need a background worker, which most free tiers don't include)
