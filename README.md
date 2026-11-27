# News Alert Bot

A Telegram bot that sends people news alerts for the topics they choose (Cybersecurity, Technology, Sports, War & Conflict, Politics, Business & Finance, Ethiopia, Africa, World, Science, Health, Entertainment). It reads news providers' official RSS/Atom feeds every few minutes, works out which topics each new story belongs to, drops duplicates, and alerts the people who follow those topics. Each alert has the headline, the feed's image (when it has one), a short description, the source, the publication time and a **📖 Read full article** button that opens the publisher's page. Full articles are never copied.

It's built the same way as the report bot: Python, [python-telegram-bot](https://python-telegram-bot.org), a small web admin page, Amharic and English texts, super admins from the environment, Docker Compose deployed by GitHub Actions. Data is in PostgreSQL, whose user name and password come from GitHub secrets.

## Features

**Amharic or English, per person.** The first `/start` asks **🇪🇹 አማርኛ** or **🇬🇧 English**; **🌐 ቋንቋ / Language** (or `/language`) switches any time. Every text is in [`texts_am.py`](texts_am.py) and [`texts_en.py`](texts_en.py), which define the same names (a test checks it).

**Onboarding.** After the language, new users get a topic picker: tap topics to tick them (✅ / ⬜, **☑️ Select all**, **⬜ Clear**), then **➡️ Next**. Every tap is saved at once and answered with a short pop-up ("✅ 🔐 Cybersecurity added"), and the selected count updates as you go. **➡️ Next** needs at least one topic; **Skip for now** finishes without topics and explains how to choose later. Next comes **📚 Choose your news sources**: the sources that cover the chosen topics, **all ✅ on by default**. Tap to turn one off (saved at once, with a pop-up; the last one left on can't be turned off), then **➡️ Next**, or **⏭ Skip (all sources)**. That's the last step: every alert is ⚡ instant (there is no daily digest). They get the main keyboard and straight away **10 of the latest stories** (today's for their topics first; if there aren't 10, the last 24 hours for their topics, then today's latest from other topics; **📥 Show more** for the rest of today's; once per user). Returning users aren't onboarded again and keep their settings.

**No Save needed.** Every choice is saved as it's tapped. Someone who picks topics (or CVE reports) but never taps ➡️ Next has their choices treated as saved after `ONBOARDING_AUTOSAVE_MINUTES` (10) without touching the bot: the remaining steps take the defaults (their sources as chosen, instant alerts), they're told "Your choices are saved", get the main keyboard, and get today's news they haven't received. People who picked nothing stay in onboarding. **📥 Latest news** fetches more of today's news any time.

**Keyboard and commands.** Everyone gets **📥 Latest news**, **🛡 CVE reports**, **🗞 My topics**, **⚙️ Settings**, **⏸ Pause alerts / ▶️ Resume alerts**, **📚 Sources**, **❓ Help**, **💬 Contact us** and **🌐 Language**; admins also get **🛠 Admin**. Commands: `/start`, `/latest`, `/cve`, `/interests`, `/settings`, `/pause`, `/resume`, `/sources`, `/language`, `/help`, `/support`, `/admin` (admins), `/backup` (super admins). Menus update the same message instead of sending new ones. Menus and checkbox lists (✅ / ⬜) are laid out as a grid ([`keyboards.py`](keyboards.py)): three buttons to a row when the labels are short, two when they're medium, one when a label is too long to share a row (in checkbox lists those go last). Amharic letters count as wider, so Amharic menus get fewer per row when needed.

**Settings** (`/settings`): pause / resume; a daily alert limit (10, 20, 30, 50, 100 or none; default `DEFAULT_MAX_PER_DAY`); news language (all, English only, Amharic only); **📚 Sources** (the same screen as **📚 Sources** / `/sources`: turn sources on or off; one switch per publisher, new sources start on). In 🗞 My topics and 📚 Sources each tap is saved at once with a pop-up, and **✅ Done** closes the picker; **🔕 Mute topics** (a muted topic stays in the list but sends nothing). Changes apply right away, including to alerts already waiting to be sent.

**Alerts.** One message per story:

```
🔐 Cybersecurity
Critical vulnerability discovered in popular software

Two to four sentences from the provider's own description.

📰 Source: The Hacker News
🕒 Published: 27 Nov 2026, 10:30
[📖 Read full article]  [🔕 Mute Cybersecurity]
```

- It's a photo with a caption when the feed gives an image, otherwise text. If Telegram can't use the image, the alert is sent as text instead.
- Nothing is invented: no image, date or summary the feed didn't provide. With no publication time it says **Found:** and when we found it. Provider text is stripped of HTML and escaped.
- The same story is sent once per person, even when several providers carry it.
- **No backlog**: only stories found after someone chose a topic are sent to them, stories older than `MAX_ARTICLE_AGE_HOURS` (12) when found are never sent, and a newly added provider's first fetch is only stored.
- There is no daily digest: every alert is sent as soon as the story is found. (The digest was removed; migration `010` switched digest users to instant alerts, and `/digest` or an old 📰 Digest button now just says so.)
- Paused users get nothing, and stories found while paused aren't sent later. People who block the bot are noticed and not messaged again until they write to it.

**📥 Latest news** (or `/latest`) first re-checks your sources (those covering your topics, minus any you turned off) that weren't checked in the last minute, waiting up to 12 s, so brand-new stories come straight away; then it sends today's stories (since 00:00 `TIMEZONE`) for your topics that you haven't received yet, newest first, 5 at a time with a **📥 Show more** button. It includes stories that weren't alerted: found while you were paused, over your daily limit, or found before you chose the topic. Muted topics are left out. Each story still comes once: a pulled story is never alerted later, and tapping again only brings new ones. Pulled stories don't count toward the daily limit. If one fails to send, the next tap tries it again. An alert the sender is about to deliver is left to it, so nothing arrives twice.

**🛡 CVE reports** (or `/cve`) are alerts for newly published vulnerabilities in the technologies a user runs. They're also a checkbox in the topic list (onboarding and 🗞 My topics); ticking it during onboarding adds a "Which technologies do you use?" step before sources. They're kept apart from the Cybersecurity topic:
- **Technologies**: tick them on **🧩 Popular technologies** (about 60, in groups: operating systems, web servers, browsers, databases, network devices, business apps, DevOps, CMS; ◀️ ▶️ between groups), type your own with **✍️ Add your own** (`nginx, Windows Server 2022, FortiGate`; a name in the catalog ticks the catalog entry), or choose **All technologies**. Every tap is saved at once with a pop-up; picking a technology turns CVE reports on.
- **⚠️ Minimum severity**: Critical only, High and above (default), Medium and above, or All (CVSS). CVEs CISA lists as **actively exploited** are always sent.
- **Alert**: severity and CVSS score, ⚠️ actively exploited / 💀 ransomware when CISA says so, the CVE id and description, which of your technologies matched, the publication time, **📖 Details (NVD)** and **🛡 CVE settings**.
- **📄 CSV report**: your CVE alerts as a spreadsheet (CVE, published, severity, score, exploited, ransomware, matched technology, description, NVD link).
- CVE alerts follow pause, daily limit and "never twice", and only CVEs found after you chose a technology are sent. They never go into the Cybersecurity topic, so topic followers aren't flooded with the ~400 CVEs published a day.
- Data: NIST's [National Vulnerability Database](https://nvd.nist.gov) CVE API (every 15 min) and CISA's [Known Exploited Vulnerabilities](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) catalog (hourly), both public US-government sources. A CVE seen in both is one alert; when CISA adds a CVE later, people it now qualifies for are alerted. Set `NVD_API_KEY` (optional secret) for NIST's higher rate limit.
- **Matching**: once a CVE has product data (NIST adds it when it analyses the CVE, usually days after publication; CISA's catalog has vendor and product), only the vulnerable products count, so a WordPress plugin bug that merely mentions nginx doesn't match nginx. Platforms a product only runs on are left out ("Windows" doesn't fire on every Windows app), and a plugin keeps its platform, so "WordPress" catches WordPress plugin bugs. Before NIST's analysis the CVE's description is used. Every NVD check also asks for CVEs NIST updated (for CVEs published in the last `ARTICLE_RETENTION_DAYS`): when product data, a severity or CISA's "exploited" flag arrives, the CVE is matched again and people it now qualifies for are alerted, still never twice. Each alert says how it matched: *NVD product data* or *mentioned in the description* (also in the CSV's `matched_by` column). Rejected CVEs are skipped; CVEs with no severity yet go only to users who chose "All" until NIST scores them. The catalog is in [`seed/technologies.json`](seed/technologies.json).

Topic choices are only used to pick alerts. They aren't used to guess anyone's views or for anything else.

**Admins** in Telegram (**🛠 Admin** / `/admin`): today's numbers (users, sources, articles, duplicates, articles waiting for review, queue size and age, sent / failed / skipped); **📡 Sources** (turn a provider on or off, see its last success and error, **⬇️ Fetch now**); **🗂 Topics** (turn a topic off for everyone); **⚠️ Failed alerts** with **🔁 Retry all**. Admins are the `SUPER_ADMIN_IDS` plus anyone made admin on the web page.

**Super admins** also get `/backup`: a zip with one CSV per table (`users.csv`, `providers.csv`… open in Excel; all of them restore with `COPY`) sent to every super admin at 00:00 `TIMEZONE` each night (can be turned off), plus **📦 Back up now**.

**Web admin page** (`http://<server>:<ADMIN_PORT>`, 8000 unless set, login `ADMIN_USERNAME` / `ADMIN_PASSWORD`):
- **Overview**: the numbers above, every provider with its state, topics and confidences, interval, last success, last error and article count. Enable / disable, fetch now, edit the interval, topics and notes, and **add a provider** (added disabled).
- **Topics**: turn topics on or off, edit their keywords and weights, add a topic.
- **Articles**: newest articles with their topics, confidence and the reason (which keywords matched); filter by provider or to **articles with no confident topic**. **Correct the topics** of an article by hand: if it's recent, people following a newly added topic get it (still never twice).
- **Failed alerts**: what failed and why, retry one or all.
- **Users**: search, 10 users per page with page links; each user's **preferences** (topics, with muted ones struck through; CVE reports; alerts on or paused; daily limit; language and news language; and, expandable, the sources they switched off and their CVE severity and technologies); make or remove admins; disable, enable or delete a user.

## How news is collected

```
providers (RSS/Atom) → adapter → validate & normalize → deduplicate → categorize → store
                                                                         ↓
              Telegram ← sender (queue, retries, rate limit) ← notifications ← match users
```

1. **Collector** ([`collector.py`](collector.py)) wakes every 15 s and fetches each enabled provider whose time has come (`polling_interval_seconds`: 3 min for news feeds, 5–10 min for slower ones), so stories usually arrive within a few minutes of publication, four at a time. Requests use the provider's ETag / Last-Modified, so an unchanged feed costs almost nothing. A failing provider is retried with exponential backoff (up to 6 h) and never holds up the others.
2. **Adapters** turn a provider's response into `RawArticle`s. RSS and Atom are built in; another kind of source (an official API) is a new class with one `fetch(provider)` method, registered in `ADAPTERS`, and a provider row with that `adapter_key`.
3. **Validation** ([`news.py`](news.py)): a title and a safe http(s) link are required; HTML is removed; URLs are made canonical (tracking parameters, fragments, default ports dropped); future dates are dropped; the summary is the provider's own description, cut to about four sentences.
4. **Duplicates**: same item id from the same feed → skipped. Same canonical URL from another feed → the existing article also gets that feed's topics (so a BBC story in both *World* and *Africa* reaches both audiences once). Near-identical headline within `DEDUP_WINDOW_HOURS` → stored as part of the same **story cluster**. Short or merely similar headlines aren't merged. Each person gets at most one alert per story cluster, enforced by a unique index, so retries can't double-send.
5. **Topics** come from three kinds of evidence, combined (1 − Π(1 − evidence)): the provider's default topics (e.g. BBC Sport → Sports 0.95), keywords in the headline (full weight), in the summary (60%) and in the provider's tags (80%). An article counts for a topic at `CLASSIFY_MIN_CONFIDENCE` (0.6) or more, so one weak word ("patch" in the summary: 0.18) isn't enough. Each result keeps its reason for the admin page. Amharic keywords match inside words (ኢትዮጵያ matches በኢትዮጵያ). War & Conflict uses specific phrases ("Israeli strikes", "strike kills", "Rapid Support Forces", "war crimes"…) rather than the bare word "strike", so labour strikes, fires and crashes aren't tagged as war; general words like "killed" or "attack" only count a little. Articles with no confident topic aren't sent; they wait on **Articles → no confident topic** for an admin. `RuleClassifier` sits behind a `Classifier` interface so an AI classifier can be added later.
6. **Matching** queues one notification per person who is active, not paused, onboarded, follows (and hasn't muted) one of the article's confident topics since before it was found, hasn't turned its source off, reads its language, and is under their daily limit.
7. **Sender** ([`delivery.py`](delivery.py)) sends queued alerts at up to `MESSAGES_PER_SECOND`, re-checking each person first. Telegram's `retry_after` pauses sending; network errors retry with backoff (30 s, 1 min, 2 min…) up to `MAX_SEND_ATTEMPTS`; permanent errors are marked failed; "bot was blocked" marks the person blocked and drops their queue.

Articles older than `ARTICLE_RETENTION_DAYS` (30) are deleted with their alert history.

## Providers and licensing

Topics and providers start from [`seed/categories.json`](seed/categories.json) and [`seed/providers.json`](seed/providers.json). They're copied into the database on the first start; later starts only add new keys, so changes made on the admin page stay. After that, manage them on the admin page; no code changes needed.

Only official RSS/Atom feeds that publishers offer for syndication are used. The bot stores the headline, the feed's short description, the image URL and the link, and sends people to the publisher for the full story. It doesn't scrape pages, log in, or get around paywalls or blocks. Every feed below was fetched on 2026-11-27:

| Provider | Feeds | Topics | State |
| --- | --- | --- | --- |
| BBC News / BBC Sport | World, Africa, Technology, Business, Politics, Science, Health, Entertainment, Sport | one each | on |
| BBC News አማርኛ | Amharic | Ethiopia 0.5 (+ keywords) | on |
| Al Jazeera | all news | World | on |
| UN News | all news | World | on |
| The Hacker News, BleepingComputer, CISA advisories | | Cybersecurity | on |
| WHO | news | Health | on |
| NASA | news releases | Science | on |
| The Reporter Ethiopia | site feed | Ethiopia | on |
| Fana Broadcasting | English and Amharic site feeds | Ethiopia | on |
| Ethiopia Observer, Ethiopia Insight | site feeds | Ethiopia | on |
| Capital Ethiopia, Addis Fortune | site feeds (business weeklies) | Ethiopia, Business 0.5 | on |
| allAfrica | Ethiopia headlines (aggregates many Ethiopian publishers) | Ethiopia | on; check allAfrica's terms for commercial use |
| ሪፖርተር (The Reporter, Amharic) | site feed | Ethiopia | off: feed hadn't updated since 1 Nov |
| BBC News | Middle East, Europe | World (conflict via keywords) | on |
| The Guardian | Ukraine, Middle East | World, War & Conflict 0.4 | on; feeds are for non-commercial use with attribution |
| UN News | Peace and security | World, War & Conflict 0.4 | on |
| The Kyiv Independent | site feed | World, War & Conflict 0.4 | on |
| International Crisis Group | conflict analysis | War & Conflict | on |
| France 24 | World, Africa, Sport | World / Africa / Sports | on |
| DW | World | World | on |
| BBC Sport | Football, Athletics, Tennis, Formula 1, Basketball | Sports | on (one switch with the main BBC Sport feed) |
| The Guardian | Sport, Football | Sports | on; non-commercial use with attribution |
| Sky Sports | News, Football | Sports | on |
| ESPN | Soccer | Sports | on |
| Soccer Ethiopia | Ethiopian football, Amharic | Sports, Ethiopia 0.5 | on |
| Krebs on Security, Ars Technica, ESPN (main) | | Cybersecurity / Technology / Sports | off (choose to enable) |
| Addis Standard, Walta, Addis Zeybe | site feeds | Ethiopia | **off: Cloudflare blocks automated clients**; left off, not worked around |

**Before running this as a public or commercial service**, read each publisher's terms for their feeds and keep a note in the provider's **Notes** field. Some feeds are offered for personal, non-commercial use or ask for specific attribution. If the terms are unclear, turn the provider off.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # then edit .env (local development only)
docker run -d --name news-pg -p 127.0.0.1:5432:5432 -e POSTGRES_USER=newsbot -e POSTGRES_PASSWORD=<the POSTGRES_PASSWORD in .env> -e POSTGRES_DB=newsbot postgres:16.4-alpine
.venv/bin/python main.py
```

(Or run everything with `docker compose up -d --build`, which starts PostgreSQL too.)

**Migrations run at every start.** Before anything else, the bot waits for the database and runs each `migrations/NNN_name.sql` it hasn't run yet, in order, recording it in `schema_migrations`; the log says `Database schema up to date (version …)` or which ones it applied. Pending migrations run in one transaction, so a failing one changes nothing and the bot doesn't start (fix it and deploy again). To change the tables, add a new numbered file (e.g. `002_add_quiet_hours.sql`); never edit one that has been deployed. `python db.py` runs the migrations without starting the bot. Then the topics and providers new in `seed/` are added.

1. Create a bot with [@BotFather](https://t.me/BotFather) and put its token in `BOT_TOKEN`.
2. Put your Telegram ID in `SUPER_ADMIN_IDS` (from @userinfobot).
3. Set `ADMIN_PASSWORD` and a random `SECRET_KEY`.
4. Run `main.py`. It starts the bot (long polling), the collector, the sender, the onboarding auto-save, backups and the admin page in one process.
5. Send `/start` to the bot. The first fetch of each provider only stores articles; alerts start with stories found after that.

Tests: `.venv/bin/python -m pytest -q`. They need a PostgreSQL **test** database (`POSTGRES_DB` must end in `test`; every table is emptied between tests), for example `docker run -d -p 127.0.0.1:5433:5432 -e POSTGRES_USER=newsbot -e POSTGRES_PASSWORD=newsbot -e POSTGRES_DB=newsbot_test postgres:16.4-alpine` and `POSTGRES_PORT=5433`. They cover normalization, URL handling, SSRF checks, duplicates, categorization, the full collect → match → send path (photo fallback, `retry_after`, retries, blocked users, daily limits, muted topics), the Telegram handlers (driven through python-telegram-bot with a fake Bot API), and the admin page.

## Deploy with GitHub Actions

Same as the report bot: all production configuration lives in GitHub (**Settings → Secrets and variables → Actions**) and nothing is edited on the server. [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs on every push to `main` (or *Actions → Deploy → Run workflow*). It compiles, runs the tests, builds the image and validates the compose file; then over SSH it copies the code, writes `.env`, backs up the database with `pg_dump` to `backups/` (keeps 14), starts PostgreSQL and sets its password from the secret, runs `docker compose up -d --build`, and waits for `/ready`.

**Prepare the server and deploy key**: exactly as for the report bot (Docker + compose plugin + rsync, an `ed25519` deploy key, `ssh-keyscan` output for `DEPLOY_KNOWN_HOSTS`).

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` | the server and the deploy key |
| `BOT_TOKEN` | token from @BotFather (a different bot from the report bot) |
| `ADMIN_USERNAME` | web admin page username, e.g. `admin` |
| `ADMIN_PASSWORD` | web admin page password (12+ characters): `python3 -c "import secrets; print(secrets.token_urlsafe(16))"` |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_USER` | database user name, e.g. `newsbot` (lowercase letters, digits, `_`) |
| `NVD_API_KEY` | optional: NIST NVD API key for CVE reports ([request one](https://nvd.nist.gov/developers/request-an-api-key)) |
| `POSTGRES_PASSWORD` | database password: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` (no `'` or line breaks) |

| Variable | Default | Value |
| --- | --- | --- |
| `SUPER_ADMIN_IDS` | (required) | your Telegram user ID(s), comma separated |
| `BOT_NAME` | `NewsPulse` | name used in the bot's messages |
| `POSTGRES_DB` | `newsbot` | database name |
| `TIMEZONE` | `Africa/Addis_Ababa` | times shown to users, "today" |
| `CLASSIFY_MIN_CONFIDENCE` | `0.6` | how sure the classifier must be |
| `MAX_ARTICLE_AGE_HOURS` | `12` | older stories are never alerted |
| `ARTICLE_RETENTION_DAYS` | `30` | how long articles are kept (0 = forever) |
| `DEFAULT_MAX_PER_DAY` | `30` | daily alert limit for new users (0 = none) |
| `MESSAGES_PER_SECOND` | `20` | sending speed (Telegram allows about 30) |
| `DOMAIN` | (none) | serve the admin page at `https://DOMAIN` through Caddy |
| `ADMIN_PORT` | `8000` | port of the admin page; it listens on all the server's interfaces (`0.0.0.0`) |
| `DEPLOY_PORT` / `DEPLOY_PATH` | `22` / `news-bot` | SSH port, folder in the SSH user's home |

**About the database secrets.** PostgreSQL runs in its own container (`news-bot-postgres`) with its data in the `pg-data` volume, and isn't published on any port: only the bot container reaches it. It's created with `POSTGRES_USER` / `POSTGRES_PASSWORD` on the first deploy. Changing `POSTGRES_PASSWORD` later is fine: every deploy sets the database password to the secret before starting the bot. Don't change `POSTGRES_USER` or `POSTGRES_DB` after the first deploy (the existing database keeps the old ones); to move to new ones, restore a backup into a fresh volume.

**Opening the admin page.** It listens on all the server's network interfaces (`0.0.0.0`) at `ADMIN_PORT`, so it's at `http://<server ip>:<ADMIN_PORT>`; allow that port in the server's firewall. It's plain HTTP, so the login password travels unencrypted: limit the port to the addresses that need it, or set `DOMAIN` to serve it over HTTPS through Caddy.

If it runs on the same server as the report bot, give it a different `ADMIN_PORT` (e.g. `8001`) and `DEPLOY_PATH`. The compose project and container names are already different.

## Operations

| Endpoint | What |
|---|---|
| `/health` | the process is up (Docker health check) |
| `/ready` | database reachable, Telegram polling, collector and sender loops alive in the last 5 min (503 otherwise; used by the deploy) |
| `/metrics` | Prometheus text: fetch success / failure per provider, articles fetched / accepted / duplicate / same story / invalid / low confidence, alerts queued / sent / failed / retried / skipped, rate-limit responses, image fallbacks, publication-to-delivery time, plus queue size and age and the admin numbers. Caddy doesn't expose `/metrics` or `/ready`. |

Logs (`docker compose logs -f bot`) record fetches, failures and every admin change. Tokens, API keys and message contents aren't logged.

| Task | Command (in `~/news-bot`) |
|---|---|
| Logs | `docker compose logs -f bot` |
| Restart | `docker compose restart bot` (stops gracefully: workers, then Telegram) |
| Stop | `docker compose down` (data stays in the `pg-data` volume; `down -v` would delete it) |
| Back up now | `docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/manual.dump` |
| Restore a backup | `docker compose stop bot`, then `docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backups/<file>.dump`, then `docker compose start bot` |
| Database shell | `docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point: bot, collector, sender, onboarding auto-save, backups and web page in one process |
| `bot.py` | Telegram handlers: onboarding, topics, settings, sources, CVE reports, admin, backup |
| `collector.py` | Provider adapters (`NewsProvider`, `RssAdapter`), polling, backoff, dedup, matching |
| `news.py` | Feed parsing, cleaning, canonical URLs, duplicate detection, `RuleClassifier` |
| `delivery.py` | Alert format, `Sender` (queue, retries, rate limits) |
| `netsafe.py` | Safe fetching: public addresses only, redirects re-checked, size and time limits |
| `db.py`, `migrations/` | PostgreSQL storage; numbered SQL migrations applied in order on start |
| `seed/` | Initial topics (with keywords) and providers |
| `texts_am.py`, `texts_en.py`, `texts.py` | Every text the bot shows, in Amharic and English |
| `web.py`, `templates/` | Web admin page, `/health`, `/ready`, `/metrics` |
| `keyboards.py` | Lays inline buttons out in a 1–3 column grid |
| `cves.py`, `seed/technologies.json` | CVE reports: NVD and CISA KEV adapters, the technology catalog |
| `metrics.py`, `backup.py`, `config.py` | Counters, zipped backups, settings from `.env` |
| `tests/` | Tests and feed fixtures |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Container deployment (optional HTTPS via Caddy) |

## Design choices and limits

- **Python and one process** like the report bot (the original plan said Go), with **PostgreSQL** as in the plan. The modules keep the same boundaries (bot / collector / classifier / delivery / storage), so the sender or collector can move to its own process later (`COLLECTOR_ENABLED` / `DELIVERY_ENABLED` already turn them off). The bot uses one connection on its event loop, waits for the database at start and reconnects if PostgreSQL restarts.
- **Users are keyed by Telegram user ID**, never by username.
- **Not done yet**: quiet hours, per-user time zones (everyone uses `TIMEZONE`) and an AI classifier (the interface is there; nothing calls an AI service). Feeds' robots.txt isn't checked automatically; official feeds are meant to be fetched, and providers are reviewed by hand before they're enabled.
- **Feed fetching** only reaches public IP addresses and re-checks every redirect. A DNS server that answers differently between the check and the connection could slip past; images are fetched by Telegram, not by this server.
