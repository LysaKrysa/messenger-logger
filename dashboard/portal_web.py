"""Owner-only web routes. Every account is resolved from an independent session."""
import hmac
from pathlib import Path
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from .portal_auth import PortalAuth, LINK_SECONDS, SESSION_SECONDS
from .portal_i18n import text
from .store import DashboardError
from .connection_alerts import ConnectionAlerts
from telegram_i18n import tr
from .message_archive import MessageArchive
from .capture_health import CaptureHealth

COOKIE, CLAIM_COOKIE = "__Host-messenger_portal", "__Host-messenger_portal_claim"


def install(app, store, origin, deliveries, status_root):
    portal = Blueprint("portal", __name__)
    auth = PortalAuth(store)
    app.extensions["portal_auth"] = auth
    reconnects = ConnectionAlerts(store, deliveries.api, origin=origin)
    archive, capture_health = MessageArchive(store), CaptureHealth(store)
    app.extensions.update(message_archive=archive, capture_health=capture_health)

    def language():
        return getattr(g, "portal_language", "pl")

    def safe_csrf(token, supplied):
        return isinstance(supplied, str) and supplied.isascii() and hmac.compare_digest(supplied, auth.csrf(token))

    @portal.before_request
    def owner():
        g.portal_language = "en" if request.args.get("lang") == "en" else "pl"
        public = {"portal.login", "portal.claim", "portal.login_status", "portal.complete"}
        if request.endpoint in public:
            if request.method == "POST" and request.headers.get("Origin") != origin:
                abort(403)
            return
        token = request.cookies.get(COOKIE)
        row = auth.session(token)
        if row is None:
            return redirect(url_for("portal.login"))
        g.owner, g.portal_token = row, token
        g.portal_language = store.telegram_language(row["chat_id"])
        g.portal_csrf = auth.csrf(token)
        if not store.take_limit("portal-actions:" + row["id"], 180, 60):
            abort(429)
        if request.method == "POST" and (request.headers.get("Origin") != origin or not safe_csrf(token, request.form.get("csrf"))):
            abort(403)

    @portal.context_processor
    def context():
        row = getattr(g, "owner", None)
        bot = store.bot()
        destination = "instagram_dashboard" if row and row["platform"] == "instagram" else "dashboard"
        return {"pt": lambda key, **values: text(language(), key, **values), "portal_language": language(),
                "owner": row, "display_name": auth.profile(row) if row else "",
                "csrf": getattr(g, "portal_csrf", ""), "bot_url": "https://t.me/" + bot["username"] + "?start=" + destination if bot else None}

    @app.template_filter("portaldate")
    def date(value):
        return datetime.fromtimestamp(value, ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y %H:%M %Z") if value else text(language(), "none")

    @app.template_filter("portalbytes")
    def size(value):
        if value is None:
            return "—"
        return f"{value / 1024**2:.1f} MiB" if value < 1024**3 else f"{value / 1024**3:.2f} GiB"

    def claim_row():
        token = request.cookies.get(CLAIM_COOKIE)
        row = auth.claim_row(token)
        if not row:
            abort(401)
        if request.method == "POST" and not safe_csrf(token, request.headers.get("X-Portal-CSRF")):
            abort(403)
        return token, row

    @portal.get("/me/login")
    def login():
        keys = ("login_wait", "login_error", "login_approved", "login_continue")
        return render_template("portal_login.html", boot={key: text(language(), key) for key in keys})

    @portal.post("/me/api/claim")
    def claim():
        if not store.take_limit("portal-claim:" + store.secret(request.remote_addr or "unknown"), 12, 900):
            abort(429)
        data = request.get_json(silent=True)
        if request.content_length is None or request.content_length > 1024 or not isinstance(data, dict) or set(data) != {"token"}:
            abort(400)
        token = auth.claim(data["token"])
        response = jsonify(ok=True)
        response.set_cookie(CLAIM_COOKIE, token, max_age=LINK_SECONDS, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    @portal.get("/me/api/login-status")
    def login_status():
        token, row = claim_row()
        if not store.take_limit("portal-status:" + store.secret(token), 60, 60):
            abort(429)
        result = {"state": row["state"], "code": row["code"], "csrf": auth.csrf(token)}
        if row["state"] == "approved":
            account = store.account(row["account_id"])
            result.update(name=auth.profile(account), owner=row["owner_chat"])
        return jsonify(result)

    @portal.post("/me/api/complete")
    def complete():
        token, _ = claim_row()
        session = auth.complete(token)
        response = jsonify(redirect=url_for("portal.home"))
        response.set_cookie(COOKIE, session, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="Strict", path="/")
        response.delete_cookie(CLAIM_COOKIE, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    def notice():
        key = request.args.get("saved")
        return text(language(), key) if key in {"saved", "capture_saved", "quiet_saved", "reconnect_sent", "reconnect_unavailable"} else None

    @portal.get("/me")
    def home():
        row, now = g.owner, store.clock()
        worker = store.setting("worker_status:" + row["id"], {})
        fresh = worker.get("revision") == row["revision"] and 0 <= now - worker.get("updated", 0) <= 90
        try:
            manager = json.loads((Path(status_root) / (row["id"] + ".json")).read_text())
            attention = manager.get("revision") == row["revision"] and manager.get("state") == "attention" and 0 <= now - manager.get("updated", 0) <= 120
        except (OSError, ValueError, TypeError):
            attention = False
        connection = "paused" if not row["desired"] else "connected" if fresh and worker.get("connected") else "attention" if attention else "reconnecting" if fresh else "unknown"
        stats = store.setting("worker_stats:" + row["id"], {})
        if stats.get("pair_epoch") != row["pair_epoch"] or not 0 <= now - stats.get("updated", 0) < 259200:
            stats = {}
        stats_fresh = stats.get("revision") == row["revision"] and 0 <= now - stats.get("updated", 0) <= 90
        with store.db() as db:
            counts = dict(db.execute("SELECT outcome,COUNT(*) FROM delivery_history WHERE account_id=? AND pair_epoch=? AND facebook_id=? AND chat_id=? AND expires>? GROUP BY outcome", (row["id"], row["pair_epoch"], row["facebook_id"], row["chat_id"], now)).fetchall())
            latest = db.execute("SELECT outcome FROM delivery_history WHERE account_id=? AND pair_epoch=? AND expires>? ORDER BY updated DESC,rowid DESC LIMIT 1", (row["id"], row["pair_epoch"], now)).fetchone()
            last_sent = db.execute("SELECT MAX(updated) FROM delivery_history WHERE account_id=? AND pair_epoch=? AND outcome='sent' AND expires>?", (row["id"], row["pair_epoch"], now)).fetchone()[0]
        last = latest[0] if latest else None
        return render_template("portal_home.html", connection=connection, worker=worker, worker_fresh=fresh,
                               stats=stats, stats_fresh=stats_fresh, delivery_counts=counts, last_delivery=last,
                               last_sent=last_sent,
                               controls=store.controls(row), now=now, notice=notice())

    @portal.post("/me/capture")
    def capture():
        action = request.form.get("action")
        if action not in {"pause", "resume"} or not store.user_control(g.owner["chat_id"], action, expected=g.owner):
            raise DashboardError("Capture setting unavailable.")
        return redirect(url_for("portal.home", saved="capture_saved"))

    @portal.route("/me/health", methods=["GET", "POST"])
    def health():
        if request.method == "POST":
            capture_health.preference(g.owner, request.form.get("alerts") == "on")
            return redirect(url_for("portal.health", saved="saved"))
        return render_template("portal_health.html", health=capture_health.view(g.owner),
                               stats=store.setting("worker_stats:" + g.owner["id"], {}), notice=notice())

    @portal.get("/me/messages")
    def messages():
        if not store.take_limit("archive-view:" + g.owner["id"], 30, 60):
            abort(429)
        archive.cleanup()
        chats = archive.conversations_for(g.owner, request.args.get("chat_q", ""))
        filters = {key: request.args.get(key, "") for key in ("q", "sender", "from", "to", "chat_q")}
        filters.update(status=request.args.get("status", "all"), kind=request.args.get("kind", "all"), offset=request.args.get("offset", "0"))
        selected = request.args.get("conversation") or (chats[0]["id"] if chats else None)
        values, next_page = archive.page(g.owner, selected, filters) if selected else ([], None)
        sync = store.setting("archive_sync:" + g.owner["id"], {})
        def page_url(page):
            params = dict(filters)
            params.update(conversation=selected, offset=page)
            return url_for("portal.messages", **params)
        return render_template("portal_messages.html", conversations=chats, selected=selected, filters=filters,
                               messages=values, next_page=next_page, page_url=page_url,
                               sync=sync if sync.get("pair_epoch") == g.owner["pair_epoch"] else {})

    @portal.route("/me/settings", methods=["GET", "POST"])
    def settings():
        row = g.owner
        if request.method == "POST":
            value = {"mode": request.form.get("mode"), "media": request.form.get("media") == "on", "view_once": request.form.get("view_once") == "on"}
            if not store.user_control(row["chat_id"], "preferences", value, expected=row):
                raise DashboardError("Preferences unavailable.")
            return redirect(url_for("portal.settings", saved="saved"))
        return render_template("portal_settings.html", controls=store.controls(row), now=store.clock(), notice=notice())

    @portal.post("/me/quiet")
    def quiet():
        value = request.form.get("duration")
        if value not in {"0", "3600", "14400", "86400"} or not store.user_control(g.owner["chat_id"], "quiet", int(value), expected=g.owner):
            raise DashboardError("Quiet preference unavailable.")
        return redirect(url_for("portal.settings", saved="quiet_saved"))

    @portal.route("/me/profile", methods=["GET", "POST"])
    def profile():
        if request.method == "POST":
            auth.update_profile(g.owner, request.form.get("name"), request.form.get("language"))
            return redirect(url_for("portal.profile", saved="saved"))
        return render_template("portal_profile.html", notice=notice())

    @portal.post("/me/logout")
    def logout():
        auth.revoke(g.owner["id"], g.portal_token, pair_epoch=g.owner["pair_epoch"])
        response = redirect(url_for("portal.login", lang=language()))
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    @portal.post("/me/sessions/revoke")
    def revoke():
        auth.revoke(g.owner["id"], pair_epoch=g.owner["pair_epoch"])
        return logout()

    @portal.post("/me/reconnect")
    def reconnect():
        if g.owner["platform"] == "instagram":
            raise DashboardError("Ask the administrator to refresh this Instagram session.")
        # Resolve the current binding again before issuing. The existing refresh
        # gate enforces offline state, same Facebook identity and Telegram owner.
        with store.db() as db:
            PortalAuth.bound(db, {"account_id": g.owner["id"], "pair_epoch": g.owner["pair_epoch"], "owner_chat": g.owner["chat_id"], "facebook_id": g.owner["facebook_id"]})
        saved = "reconnect_unavailable"
        try:
            url = reconnects.issue(g.owner["chat_id"], expected=g.owner)
            deliveries.api.json("sendMessage", {"chat_id": g.owner["chat_id"], "text": tr(language(), "reconnect_link", url=url), "link_preview_options": {"is_disabled": True}})
            saved = "reconnect_sent"
        except Exception:
            pass  # Never replay an ambiguous reconnect-link send.
        return redirect(url_for("portal.home", saved=saved))

    def offset():
        value = request.args.get("offset", "0")
        if not re.fullmatch(r"[0-9]{1,4}", value) or int(value) > 4980:
            abort(400)
        return int(value)

    @portal.route("/me/conversations", methods=["GET", "POST"])
    def conversations():
        row, catalogue = g.owner, deliveries.conversations
        page = offset()
        if request.method == "POST":
            if request.form.get("action") == "default":
                value = request.form.get("capture_default")
                if value not in {"all", "include"}:
                    abort(400)
                catalogue.update(row, default=value == "all")
            elif request.form.get("action") == "rule":
                value = request.form.get("capture")
                if value not in {"inherit", "include", "exclude"}:
                    abort(400)
                catalogue.update(row, request.form.get("conversation", ""), include={"inherit": None, "include": True, "exclude": False}[value], mode=request.form.get("forward", ""))
            else:
                abort(400)
            return redirect(url_for("portal.conversations", offset=page, saved="saved"))
        return render_template("portal_conversations.html", policy=store.filters(row["id"]), conversations=catalogue.rows(row, page), offset=page, notice=notice())

    @portal.get("/me/deliveries")
    def history():
        page = offset()
        outcome = request.args.get("outcome")
        return render_template("portal_deliveries.html", deliveries=deliveries.rows(g.owner, page, 20), offset=page,
                               outcome=outcome if outcome in {"sent", "failed", "uncertain"} else None)

    @portal.route("/me/deliveries/<ident>/resend", methods=["GET", "POST"])
    def resend(ident):
        value = deliveries.get(g.owner, ident)
        if value is None:
            abort(404)
        if request.method == "POST":
            result = deliveries.resend(g.owner, request.form.get("confirmation", ""), acknowledged=request.form.get("acknowledge") == "yes", expected_delivery=ident)
            return redirect(url_for("portal.history", outcome=result["outcome"] if result else "uncertain"))
        return render_template("portal_resend.html", delivery=value, confirmation=deliveries.intent(g.owner, ident) if value["available"] else None)

    @portal.errorhandler(Exception)
    def error(exc):
        code = exc.code if isinstance(exc, HTTPException) else 400 if isinstance(exc, DashboardError) else 500
        if request.path.startswith("/me/api/"):
            return jsonify(error=text(language(), "login_error")), code
        return render_template("portal_error.html"), code

    app.register_blueprint(portal)
