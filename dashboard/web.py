"""Admin workspace, with explicit private cookie editing and separate owner routes."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import secrets
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from logger import PLATFORM_NAMES
from .store import BOT_TOKEN, DashboardError, from_environment
from .usage import parse_config
from .usage_view import build_view


def verify_bot(token):
    if not BOT_TOKEN.fullmatch(token):
        raise DashboardError("Enter a valid BotFather token.")
    try:
        req = Request("https://api.telegram.org/bot" + token + "/getMe", data=b"{}", headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError
        payload = json.loads(raw)
        if payload.get("ok") is not True:
            raise ValueError
        return payload["result"]
    except Exception:
        raise DashboardError("Telegram could not verify the bot. Check its token and server connectivity.") from None


def create_app(store=None, *, origin=None, status_root=None, testing=False, browser_client=None, delivery_api=None):
    app = Flask(__name__)
    store = store or from_environment()
    origin = (origin or os.environ.get("DASHBOARD_ORIGIN", "https://localhost:8443")).rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise DashboardError("Configure an HTTPS dashboard origin.")
    status_root = Path(status_root or os.environ.get("DASHBOARD_STATUS_DIR", "/var/lib/messenger-dashboard-status"))
    app.config.update(SECRET_KEY=store.secret("flask-session"), TESTING=testing, MAX_CONTENT_LENGTH=160 * 1024,
                      SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_NAME="messenger_admin", TRUSTED_HOSTS=[parsed.hostname], MAX_FORM_PARTS=12,
                      MAX_FORM_MEMORY_SIZE=140 * 1024)
    app.extensions["store"] = store
    from .broker import TelegramAPI
    from .deliveries import Deliveries
    deliveries = Deliveries(store, delivery_api or TelegramAPI(store))
    app.extensions["deliveries"] = deliveries
    from .setup_web import install
    install(app, store, origin, browser_client)
    from .portal_web import install as install_portal
    install_portal(app, store, origin, deliveries, status_root)

    def status(ident):
        try:
            value = json.loads((status_root / (ident + ".json")).read_text())
            if not isinstance(value, dict):
                return {}
            value["stale"] = store.clock() - value.get("updated", 0) > 120
            return value
        except (OSError, ValueError, TypeError):
            return {"state": "unknown", "stale": True}

    @app.before_request
    def authenticate():
        if request.endpoint in {"static", "favicon"} or request.blueprint in {"setup", "portal"}:
            return None
        g.csrf = session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method == "POST":
            supplied = request.form.get("csrf", "")
            token_matches = supplied.isascii() and hmac.compare_digest(supplied, g.csrf)
            origin_matches = request.headers.get("Origin", origin).rstrip("/") == origin
            if not token_matches or not origin_matches:
                app.logger.warning("Dashboard form rejected (reason=%s; session_cookie_present=%s; token_present=%s; origin_matches=%s).",
                    "csrf_mismatch" if not token_matches else "origin_mismatch",
                    bool(request.cookies.get(app.config["SESSION_COOKIE_NAME"])), bool(supplied), origin_matches)
                abort(403)
        if request.endpoint not in {"login", "static"}:
            if not store.session(session.get("sid")):
                # Background requests and another anonymous tab must not
                # invalidate a login form that is already open in the browser.
                if "sid" in session:
                    session.pop("sid")
                return redirect(url_for("login"))

    @app.after_request
    def secure_headers(response):
        response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", "Strict-Transport-Security": "max-age=31536000",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
        if request.endpoint == "cookie_editor":
            response.headers.update({"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, noarchive"})
        return response

    @app.context_processor
    def context():
        return {"csrf": getattr(g, "csrf", ""), "bot": store.bot(), "capacity": store.max_accounts,
                "platform_names": PLATFORM_NAMES}

    @app.template_filter("filesize")
    def filesize(value):
        return f"{value / 1024 ** 3:.1f} GB" if isinstance(value, (int, float)) else "—"

    @app.template_filter("eventdate")
    def eventdate(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            try:
                sid = store.login(request.form.get("password", ""), request.remote_addr or "unknown")
                session.clear()
                session.update(sid=sid, csrf=secrets.token_urlsafe(32))
                return redirect(url_for("index"))
            except DashboardError as exc:
                error = str(exc)
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        store.session(session.get("sid"), revoke=True)
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        accounts = store.accounts()
        for account in accounts:
            account["status"] = status(account["id"])
        host = status("host")
        return render_template("index.html", accounts=accounts, host=host,
                               connected=sum(a["status"].get("ready", False) and not a["status"].get("stale") for a in accounts),
                               bot_status=store.setting("bot_status", "Waiting for the invitation receiver"))

    @app.route("/usage", methods=["GET", "POST"])
    def usage():
        if request.method == "POST":
            store.set_setting("usage_config", parse_config(request.form, store.clock()))
            return redirect(url_for("usage", saved="1"))
        return render_template("usage.html", usage=build_view(store, status_root, store.clock()), saved=request.args.get("saved") == "1")

    @app.route("/accounts/new", methods=["GET", "POST"])
    def new_account():
        if request.method == "POST":
            ident = store.add_account(request.form.get("name", ""), request.form.get("cookies", ""),
                                      platform=request.form.get("platform", "facebook"))
            return redirect(url_for("account", ident=ident))
        platform = request.args.get("platform", "facebook")
        if platform not in PLATFORM_NAMES:
            abort(400)
        return render_template("new.html", platform=platform)

    def account_page(ident, **extra):
        account = store.account(ident)
        if not account:
            abort(404)
        return render_template("account.html", account=account, status=status(ident), **extra)

    @app.get("/accounts/<ident>")
    def account(ident):
        return account_page(ident)

    def account_row(ident):
        row = store.account(ident)
        if row is None:
            abort(404)
        return row

    def page_offset():
        raw = request.args.get("offset", "0")
        if not re.fullmatch(r"[0-9]{1,4}", raw) or int(raw) > 4980:
            raise DashboardError("Invalid page.")
        return int(raw)

    @app.route("/accounts/<ident>/conversations", methods=["GET", "POST"])
    def conversations(ident):
        row = account_row(ident)
        catalogue = deliveries.conversations
        if request.method == "POST":
            if not row["chat_id"]:
                raise DashboardError("Approve Telegram pairing before configuring conversation filters.")
            if request.form.get("action") == "default":
                value = request.form.get("capture_default")
                if value not in {"all", "include"}:
                    raise DashboardError("Choose the default capture policy.")
                catalogue.update(row, default=value == "all")
            elif request.form.get("action") == "rule":
                value = request.form.get("capture")
                if value not in {"inherit", "include", "exclude"}:
                    raise DashboardError("Choose a capture setting.")
                catalogue.update(row, request.form.get("conversation", ""), include={"inherit": None, "include": True, "exclude": False}[value], mode=request.form.get("forward", ""))
            else:
                raise DashboardError("Invalid filter action.")
            return redirect(url_for("conversations", ident=ident, saved="1", offset=page_offset()))
        offset = page_offset()
        return render_template("conversations.html", account=row, policy=store.filters(ident),
                               conversations=catalogue.rows(row, offset), offset=offset, saved=request.args.get("saved") == "1")

    @app.get("/accounts/<ident>/deliveries")
    def delivery_history(ident):
        row = account_row(ident)
        offset = page_offset()
        outcome = request.args.get("outcome")
        return render_template("deliveries.html", account=row, deliveries=deliveries.rows(row, offset, 20), offset=offset,
                               outcome=outcome if outcome in {"sent", "failed", "uncertain"} else None)

    @app.route("/accounts/<ident>/deliveries/<delivery>/resend", methods=["GET", "POST"])
    def resend(ident, delivery):
        row = account_row(ident)
        value = deliveries.get(row, delivery)
        if value is None:
            abort(404)
        if request.method == "POST":
            # The token alone is insufficient: explicitly acknowledge possible
            # duplicates, then consume a pairing-bound, single-use confirmation.
            result = deliveries.resend(row, request.form.get("confirmation", ""), acknowledged=request.form.get("acknowledge") == "yes", expected_delivery=delivery)
            return redirect(url_for("delivery_history", ident=ident, outcome=result["outcome"] if result else "uncertain"))
        return render_template("resend.html", account=row, delivery=value,
                               confirmation=deliveries.intent(row, delivery) if value["available"] else None)

    @app.post("/accounts/<ident>/cookies")
    def cookies(ident):
        store.update_cookies(ident, request.form.get("cookies", ""))
        return redirect(url_for("account", ident=ident))

    @app.post("/accounts/<ident>/cookies/edit")
    def cookie_editor(ident):
        row = account_row(ident)
        action = request.form.get("action")
        if action == "load":
            account, cookie_json, stamp = store.cookie_editor(ident)
            return render_template("cookie_editor.html", account=account, cookie_json=cookie_json, stamp=stamp)
        if action == "save":
            stamp = request.form.get("stamp", "")
            if not re.fullmatch(r"[a-f0-9]{64}", stamp):
                raise DashboardError("Reopen the saved-cookie editor before saving.")
            raw = request.form.get("cookies", "")
            try:
                store.update_cookies(ident, raw, expected_stamp=stamp)
            except DashboardError as error:
                return render_template("cookie_editor.html", account=row, cookie_json=raw, stamp=stamp, error=str(error)), 400
            return redirect(url_for("account", ident=ident, cookies_saved="1"))
        raise DashboardError("Choose whether to load or save cookies.")

    @app.post("/accounts/<ident>/portal/revoke")
    def revoke_portal(ident):
        account_row(ident)
        app.extensions["portal_auth"].revoke(ident)
        return redirect(url_for("account", ident=ident))

    @app.post("/accounts/<ident>/invite")
    def invite(ident):
        return account_page(ident, invite=store.invite(ident))

    @app.post("/accounts/<ident>/<action>")
    def action(ident, action):
        store.action(ident, action, mode=request.form.get("mode", "deleted"), view_once=request.form.get("view_once") == "on")
        if action == "remove":
            return redirect(url_for("index"))
        return redirect(url_for("account", ident=ident))

    @app.route("/bot", methods=["GET", "POST"])
    def bot_settings():
        if request.method == "POST":
            token = request.form.get("token", "").strip()
            store.configure_bot(token, verify_bot(token))
            return redirect(url_for("bot_settings"))
        return render_template("bot.html", bot_status=store.setting("bot_status", "Waiting for setup"))

    @app.errorhandler(DashboardError)
    def invalid(error):
        return render_template("error.html", message=str(error)), 400

    @app.errorhandler(Exception)
    def error(error):
        code = error.code if isinstance(error, HTTPException) else 500
        message = {400: "The request was not accepted.", 403: "The form expired or its source could not be verified. Reload the page and try again.",
                   404: "This page was not found.", 413: "The submitted form is too large."}.get(code, "The operation could not be completed. Private details are hidden.")
        return render_template("error.html", message=message), code

    return app
