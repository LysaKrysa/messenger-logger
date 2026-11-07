"""Local administrator tools; secrets are prompted, never command-line arguments."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from cryptography.fernet import Fernet

from .broker import TelegramAPI
from .store import Store, DashboardError, from_environment
from .web import verify_bot


def hostname(value):
    try:
        ipaddress.ip_address(value)
        return "IP:" + value
    except ValueError:
        if len(value) <= 253 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value) and ".." not in value:
            return "DNS:" + value
    raise DashboardError("Use a valid public IP address or hostname.")


def read_env(path):
    raw = Path(path).read_text()
    if len(raw) > 262144:
        raise DashboardError("The existing configuration file is too large.")
    result = {}
    for line in raw.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value
    return result


def import_existing(store, env_file, data_dir, name, *, data_root=Path("/var/lib/messenger-accounts"), runner=subprocess.run):
    if os.geteuid() != 0:
        raise DashboardError("Existing-service import must run with sudo.")
    env = read_env(env_file)
    data = Path(data_dir)
    if not data.is_dir() or data.is_symlink():
        raise DashboardError("Use the existing logger's real data directory.")
    cookies = Store.validate_cookies(env.get("FB_SESSION_JSON", ""))
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]{1,20}", chat) or int(chat) <= 0:
        raise DashboardError("Import requires a private Telegram chat ID.")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    store.configure_bot(token, verify_bot(token))
    remote_chat = TelegramAPI(store).json("getChat", {"chat_id": chat})
    if not isinstance(remote_chat, dict) or remote_chat.get("type") != "private" or str(remote_chat.get("id")) != chat:
        raise DashboardError("The existing Telegram destination was not verified as a private chat.")
    with store.db() as db:
        if db.execute("SELECT 1 FROM accounts WHERE chat_id=? OR pending_chat=?", (int(chat), int(chat))).fetchone():
            raise DashboardError("That Telegram destination is already paired.")
    ident = store.add_account(name, json.dumps(cookies))
    target = data_root / ident
    active = runner(["systemctl", "is-active", "--quiet", "messenger-logger"], capture_output=True).returncode == 0
    try:
        if active:
            runner(["systemctl", "stop", "messenger-logger"], check=True, capture_output=True)
        target.mkdir(mode=0o700, parents=True)
        # A stopped source gives a consistent SQLite/device-state copy. Do not
        # follow archive symlinks into unrelated host paths.
        shutil.copytree(data, target / "data", symlinks=True)
        runner(["systemctl", "disable", "messenger-logger"], check=True, capture_output=True)
        with store.db() as db:
            db.execute("UPDATE accounts SET chat_id=?,desired=1,mode=?,view_once=?,revision=revision+1 WHERE id=?", (int(chat), "all" if env.get("TELEGRAM_MODE") == "all" else "deleted", int(env.get("TELEGRAM_VIEW_ONCE", "false").lower() == "true"), ident))
    except Exception:
        store.action(ident, "stop")
        if active:
            runner(["systemctl", "enable", "--now", "messenger-logger"], capture_output=True)
        raise DashboardError("Import did not finish. The original service was restored if it was running; the new account is stopped.") from None
    return ident


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("password")
    sub.add_parser("restart-active")
    imported = sub.add_parser("import-existing")
    imported.add_argument("--env-file", required=True)
    imported.add_argument("--data-dir", required=True)
    imported.add_argument("--name", default="My account")
    tls = sub.add_parser("certificate")
    tls.add_argument("--host", required=True)
    tls.add_argument("--directory", default="/etc/messenger-dashboard")
    args = parser.parse_args()
    if args.command == "certificate":
        san = hostname(args.host)
        directory = Path(args.directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "365", "-subj", "/CN=" + args.host,
                        "-addext", "subjectAltName=" + san, "-keyout", str(directory / "tls.key"), "-out", str(directory / "tls.crt")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    key_path = Path(os.environ.get("DASHBOARD_KEY_FILE", "/etc/messenger-dashboard/master.key"))
    if args.command == "init" and not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key())
    store = from_environment()
    if args.command == "restart-active":
        count = store.restart_active()
        print(str(count) + " active account(s) scheduled for restart. Stopped accounts are preserved.")
    elif args.command in {"init", "password"}:
        if args.command == "init" and store.setting("password"):
            print("Existing dashboard credentials preserved.")
            return
        password = getpass.getpass("Choose an administrator password (14+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise DashboardError("Passwords did not match.")
        store.set_password(password)
        print("Administrator password saved. Existing sign-in sessions revoked.")
    else:
        ident = import_existing(store, args.env_file, args.data_dir, args.name)
        print("Existing logger imported as account " + ident + ". Check its status in the dashboard.")
        print("Original data and its retention timer are preserved. Only the original logger service was disabled.")


if __name__ == "__main__":
    try:
        main()
    except DashboardError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        print("The administrator operation failed; private details hidden.")
        raise SystemExit(1)
