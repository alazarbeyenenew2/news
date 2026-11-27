"""Web admin page (providers, topics, articles, failed alerts, users) plus /health, /ready and /metrics."""
import hmac
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
import metrics
from db import db, now_utc, parse_iso, to_local
from netsafe import UnsafeURL, check_url_syntax
from profiles import ProfileCache

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["local"] = lambda iso: to_local(iso).strftime("%Y-%m-%d %H:%M") if iso else "—"
templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=1)


def _ago(iso: str | None) -> str:
    """'just now', '5 min ago', '3 h ago', '2 days ago' (or in the future: 'in 5 min')."""
    if not iso:
        return "never"
    seconds = int((now_utc() - parse_iso(iso)).total_seconds())
    future, seconds = seconds < 0, abs(seconds)
    if seconds < 60:
        text = "just now" if not future else "in under a minute"
        return text
    for unit, size in (("day", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            n = seconds // size
            label = f"{n} {unit}" + ("s" if unit == "day" and n != 1 else "")
            return f"in {label}" if future else f"{label} ago"
    return "just now"


templates.env.filters["ago"] = _ago
KEY = re.compile(r"^[a-z0-9_]{2,40}$")


def create_web_app(lifespan=None) -> FastAPI:
    app = FastAPI(title=f"{config.BOT_NAME} Admin", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="strict",
                       max_age=60 * 60 * 12, https_only=False)
    app.state.profiles = ProfileCache()

    def logged_in(request: Request) -> bool:
        return request.session.get("admin") is True

    def check_csrf(request: Request, token: str) -> None:
        if not hmac.compare_digest(token, request.session.get("csrf", "")):
            raise HTTPException(400, "Invalid form token, reload the page and try again.")

    def page(request: Request, name: str, **context):
        failed = db._one("SELECT COUNT(*) FROM notifications WHERE status = 'failed'")
        return templates.TemplateResponse(request, name, {"csrf": request.session.get("csrf", ""),
                                                          "bot_name": config.BOT_NAME, "failed_count": failed,
                                                          "msg": request.query_params.get("msg", ""),
                                                          "threshold": config.CLASSIFY_MIN_CONFIDENCE, **context})

    def worker(request: Request, name: str):
        return getattr(request.app.state, name, None)

    # ---------- health ----------
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request):
        checks = {}
        try:
            checks["database"] = db.ping()
        except Exception:  # noqa: BLE001
            checks["database"] = False
        telegram = worker(request, "telegram")
        checks["telegram"] = bool(telegram and telegram.running)
        for name, enabled in (("collector", config.COLLECTOR_ENABLED), ("sender", config.DELIVERY_ENABLED)):
            w = worker(request, name)
            if enabled:
                last = w.last_loop_at if w else None
                checks[name] = bool(last and (now_utc() - last).total_seconds() < 300)
        ok = all(checks.values())
        return JSONResponse({"status": "ready" if ok else "not ready", "checks": checks},
                            status_code=200 if ok else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint():
        s = db.stats()
        oldest = parse_iso(s["queue_oldest"])
        gauges = {k: v for k, v in s.items() if isinstance(v, (int, float))}
        gauges["queue_oldest_age_seconds"] = max(0, (now_utc() - oldest).total_seconds()) if oldest else 0
        return metrics.render(gauges)

    # ---------- login ----------
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None, "bot_name": config.BOT_NAME})

    @app.post("/login")
    async def login(request: Request, username: str = Form(...), password: str = Form(...)):
        ok = bool(config.ADMIN_PASSWORD) and hmac.compare_digest(
            username, config.ADMIN_USERNAME) & hmac.compare_digest(password, config.ADMIN_PASSWORD)
        if not ok:
            return templates.TemplateResponse(request, "login.html", {"error": "Wrong username or password.",
                                                                      "bot_name": config.BOT_NAME},
                                              status_code=401)
        request.session.clear()
        request.session["admin"] = True
        request.session["csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---------- dashboard / providers ----------
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        from datetime import datetime
        return page(request, "dashboard.html", stats=db.stats(), providers=db.providers(), dash=db.dashboard(),
                    categories={c.key: c for c in db.categories(active_only=False)},
                    now_local=datetime.now(config.TIMEZONE).strftime("%A %d %B %Y, %H:%M"))

    @app.post("/providers/{provider_id}/{action}")
    async def provider_action(request: Request, provider_id: int, action: str, csrf: str = Form(...),
                              interval: int = Form(0), categories: str = Form(""), notes: str = Form("")):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        provider = db.get_provider(provider_id)
        if not provider:
            raise HTTPException(404, "Provider not found")
        if action == "toggle":
            db.update_provider(provider.id, is_enabled=not provider.is_enabled, next_fetch_at=None)
            msg = f"{provider.key}: {'disabled' if provider.is_enabled else 'enabled'}."
        elif action == "fetch":
            db.update_provider(provider.id, next_fetch_at=None)
            msg = f"{provider.key}: will be fetched within a few seconds."
        elif action == "edit":
            try:
                cats = _parse_weights(categories)
            except ValueError as e:
                return _back("/", f"{provider.key}: {e}")
            if not 60 <= interval <= 86400:
                return _back("/", "Polling interval must be between 60 and 86400 seconds.")
            db.update_provider(provider.id, polling_interval_seconds=interval, categories=cats, notes=notes.strip())
            msg = f"{provider.key}: saved."
        else:
            raise HTTPException(400, "Unknown action")
        if (collector := worker(request, "collector")) is not None:
            collector.wake()
        return _back("/", msg)

    @app.post("/providers")
    async def add_provider(request: Request, csrf: str = Form(...), key: str = Form(...), name: str = Form(...),
                           base_url: str = Form(...), feed_url: str = Form(...), language: str = Form("en"),
                           interval: int = Form(900), categories: str = Form("{}"), notes: str = Form("")):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        key = key.strip().lower()
        try:
            if not KEY.match(key):
                raise ValueError("key must be 2-40 lowercase letters, digits or _")
            check_url_syntax(feed_url, https_only=False)
            check_url_syntax(base_url, https_only=False)
            cats = _parse_weights(categories)
            if not 60 <= interval <= 86400:
                raise ValueError("polling interval must be between 60 and 86400 seconds")
        except (ValueError, UnsafeURL) as e:
            return _back("/", f"Not added: {e}")
        _, added = db.seed([], [{"key": key, "name": name.strip(), "base_url": base_url.strip(),
                                 "feed_url": feed_url.strip(), "language": language.strip()[:5] or "en",
                                 "interval": interval, "enabled": False, "categories": cats, "notes": notes.strip()}])
        return _back("/", f"{key}: added (disabled). Review the publisher's terms, then enable it."
                     if added else f"{key}: a provider with this key already exists.")

    # ---------- categories ----------
    @app.get("/categories", response_class=HTMLResponse)
    async def categories_page(request: Request):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        return page(request, "categories.html", categories=db.categories(active_only=False),
                    followers=db.category_followers())

    @app.post("/categories/{category_id}/{action}")
    async def category_action(request: Request, category_id: int, action: str, csrf: str = Form(...),
                              keywords: str = Form("")):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        category = db.get_category(category_id)
        if not category:
            raise HTTPException(404, "Topic not found")
        if action == "toggle":
            db.set_category_active(category.id, not category.is_active)
            msg = f"{category.key}: {'turned off' if category.is_active else 'turned on'}."
        elif action == "keywords":
            try:
                db.set_category_keywords(category.id, _parse_weights(keywords))
            except ValueError as e:
                return _back("/categories", f"{category.key}: {e}")
            msg = f"{category.key}: keywords saved (used for new articles)."
        else:
            raise HTTPException(400, "Unknown action")
        return _back("/categories", msg)

    @app.post("/categories")
    async def add_category(request: Request, csrf: str = Form(...), key: str = Form(...), emoji: str = Form("📰"),
                           name_en: str = Form(...), name_am: str = Form(""), keywords: str = Form("{}")):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        key = key.strip().lower()
        try:
            if not KEY.match(key):
                raise ValueError("key must be 2-40 lowercase letters, digits or _")
            weights = _parse_weights(keywords)
        except ValueError as e:
            return _back("/categories", f"Not added: {e}")
        added, _ = db.seed([{"key": key, "emoji": emoji.strip()[:4] or "📰", "name_en": name_en.strip(),
                             "name_am": name_am.strip() or name_en.strip(), "keywords": weights,
                             "sort_order": 100}], [])
        return _back("/categories", f"{key}: added." if added else f"{key}: a topic with this key already exists.")

    # ---------- articles ----------
    @app.get("/articles", response_class=HTMLResponse)
    async def articles_page(request: Request, low: int = 0, provider: int = 0, page_no: int = 0):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        rows = db.recent_articles(limit=50, offset=page_no * 50, low_confidence=bool(low),
                                  provider_id=provider or None)
        return page(request, "articles.html", rows=rows, low=low, provider=provider, page_no=page_no,
                    providers=db.providers(), categories=db.categories(active_only=False))

    @app.post("/articles/{article_id}/categories")
    async def set_article_categories(request: Request, article_id: int, csrf: str = Form(...)):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        article = db.get_article(article_id)
        if not article:
            raise HTTPException(404, "Article not found")
        valid = {c.id for c in db.categories(active_only=False)}
        chosen = {int(v) for v in form.getlist("category") if str(v).isdigit() and int(v) in valid}
        db.set_manual_categories(article.id, chosen)
        queued = 0
        collector = worker(request, "collector")
        if collector is not None and not article.is_backlog:
            queued = collector.match(article.id)
            if queued and (sender := worker(request, "sender")) is not None:
                sender.wake()
        filters = {k: str(form.get(k, "")) for k in ("low", "provider", "page_no") if str(form.get(k, "")).isdigit()}
        return _back("/articles", f"Article {article.id}: categories saved; {queued} new alerts queued.", **filters)

    # ---------- notifications ----------
    @app.get("/notifications", response_class=HTMLResponse)
    async def notifications_page(request: Request):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        return page(request, "notifications.html", failed=db.failed_notifications(200))

    @app.post("/notifications/retry")
    async def retry_notifications(request: Request, csrf: str = Form(...), notification_id: int = Form(0)):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        ids = [notification_id] if notification_id else [f["id"] for f in db.failed_notifications(1000)]
        count = sum(db.retry_notification(i) for i in ids)
        if (sender := worker(request, "sender")) is not None:
            sender.wake()
        return _back("/notifications", f"{count} alerts queued again.")

    # ---------- users ----------
    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request, q: str = ""):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        rows = db.list_users(q)
        ids = [r["user"].telegram_id for r in rows]
        prefs = db.user_preferences(ids)
        telegram = worker(request, "telegram")
        profiles = await request.app.state.profiles.get_many(telegram.bot, ids) if telegram else {}
        return page(request, "users.html", rows=rows, q=q, prefs=prefs, profiles=profiles)

    @app.get("/users/{telegram_id}/photo")
    async def user_photo(request: Request, telegram_id: int):
        """The user's Telegram profile photo (small), fetched by the bot and cached."""
        if not logged_in(request):
            raise HTTPException(401)
        telegram = worker(request, "telegram")
        if telegram is None or db.get_user(telegram_id) is None:
            raise HTTPException(404)
        photo = await request.app.state.profiles.photo(telegram.bot, telegram_id)
        if photo is None:
            raise HTTPException(404)
        data, _ = photo
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400",
                                                                "X-Content-Type-Options": "nosniff"})

    @app.post("/users/{telegram_id}/{action}")
    async def user_action(request: Request, telegram_id: int, action: str, csrf: str = Form(...),
                          q: str = Form("")):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)
        user = db.get_user(telegram_id)
        if not user:
            raise HTTPException(404, "User not found")
        if user.is_super_admin:
            return _back("/users", "Super admins (SUPER_ADMIN_IDS) can't be changed here.", q=q)
        if action == "delete":
            db.delete_user(telegram_id)
            request.app.state.profiles.forget(telegram_id)
            return _back("/users", f"{user.display_name} was deleted with all their data.", q=q)
        changes = {"disable": {"status": "disabled", "notifications_enabled": False},
                   "enable": {"status": "active"},
                   "make_admin": {"role": "admin"}, "remove_admin": {"role": "user"}}
        if action not in changes:
            raise HTTPException(400, "Unknown action")
        db.update_user(telegram_id, **changes[action])
        if action == "disable":
            db.skip_queued_for_user(telegram_id, "disabled by an admin")
        telegram = worker(request, "telegram")
        if telegram is not None and action in ("make_admin", "remove_admin"):
            from bot import set_commands_for
            await set_commands_for(telegram, db.get_user(telegram_id))
        return _back("/users", f"{user.display_name}: {action.replace('_', ' ')} done.", q=q)

    return app


def _parse_weights(text: str) -> dict:
    """A JSON object of name -> number between 0 and 1."""
    text = (text or "").strip() or "{}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None
    if not isinstance(value, dict):
        raise ValueError("must be a JSON object like {\"word\": 0.8}")
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("every name must be a non-empty string")
        if not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f"weight for {k!r} must be a number from 0 to 1")
        out[k.strip()] = float(v)
    return out


def _back(path: str, msg: str, **params) -> RedirectResponse:
    """Back to one of our own pages (path is always a fixed local path), with a message."""
    query = {k: v for k, v in params.items() if v}
    return RedirectResponse(path + "?" + urlencode({**query, "msg": msg}), status_code=303)
