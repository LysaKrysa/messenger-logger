#!/usr/bin/env bash
# Install the optional multi-account dashboard alongside the existing service.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run with sudo bash server/install-dashboard.sh --host YOUR_PUBLIC_IP_OR_HOSTNAME'
  exit 1
fi
LOGGER_DASHBOARD_HOST=""
if [[ $# -eq 2 && $1 == --host ]]; then
  LOGGER_DASHBOARD_HOST="$2"
else
  echo 'Usage: sudo bash server/install-dashboard.sh --host YOUR_PUBLIC_IP_OR_HOSTNAME'
  exit 1
fi
LOGGER_SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$LOGGER_DASHBOARD_HOST" <<'PY'
import ipaddress, re, sys
host = sys.argv[1]
try:
    address = ipaddress.ip_address(host)
    if address.version != 4:
        raise SystemExit('Use an IPv4 address or DNS hostname for this installer.')
except ValueError:
    if len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host) or '..' in host:
        raise SystemExit('Invalid public IP/hostname.')
PY
command -v docker >/dev/null
docker image inspect messenger-logger:modern >/dev/null
docker run --rm --network none --mount "type=bind,source=$LOGGER_SOURCE_DIR,target=/app,readonly" messenger-logger:modern --self-test
apt-get update
apt-get install -y python3-venv openssl
# Fail before changing a working dashboard if this host cannot run the
# sandboxed browser. The smoke check uses synthetic HTML and no external login.
docker build --tag messenger-onboarding:browser "$LOGGER_SOURCE_DIR/browser"
python3 "$LOGGER_SOURCE_DIR/browser/check_container.py"
if ! id messenger-dashboard >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/messenger-dashboard --shell /usr/sbin/nologin messenger-dashboard
fi
for LOGGER_DASHBOARD_UNIT in messenger-dashboard-web messenger-dashboard-bot messenger-dashboard-manager messenger-dashboard-browser; do
  if systemctl cat "$LOGGER_DASHBOARD_UNIT" >/dev/null 2>&1; then
    systemctl stop "$LOGGER_DASHBOARD_UNIT"
  fi
done
install -d -m 755 /opt/messenger-dashboard/app
install -d -m 750 -o root -g messenger-dashboard /etc/messenger-dashboard
install -d -m 755 /etc/messenger-dashboard/docker-client
install -d -m 700 -o messenger-dashboard -g messenger-dashboard /var/lib/messenger-dashboard
install -d -m 700 /var/lib/messenger-accounts
install -d -m 755 /var/lib/messenger-dashboard-status
python3 - "$LOGGER_SOURCE_DIR" <<'PY'
from pathlib import Path
import shutil, sys
source, target = Path(sys.argv[1]), Path('/opt/messenger-dashboard/app')
modules = ('bridge_rpc.py', 'event_store.py', 'logger.py', 'view_messages.py', 'media_saver.py', 'telegram_relay.py',
           'telegram_format.py', 'telegram_i18n.py', 'conversation_filters.py', 'telegram_topics.py', 'identities.py', 'contact_names.py', 'retention.py', 'install_bridge.py',
           'check_telegram_topics.py', 'requirements-dashboard.txt', 'LICENSE', 'NOTICE', 'THIRD_PARTY.md')
for name in modules:
    shutil.copyfile(source / name, target / name)
shutil.copytree(source / 'dashboard', target / 'dashboard', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
shutil.copytree(source / 'browser', target / 'browser', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(source / 'LICENSES', target / 'LICENSES', dirs_exist_ok=True)
for path in target.rglob('*'):
    path.chmod(0o755 if path.is_dir() else 0o644)
PY
python3 -m venv /opt/messenger-dashboard/venv
/opt/messenger-dashboard/venv/bin/python -m pip install --disable-pip-version-check -r /opt/messenger-dashboard/app/requirements-dashboard.txt
cat > /etc/messenger-dashboard/service.env <<EOF
DASHBOARD_ORIGIN=https://$LOGGER_DASHBOARD_HOST:8443
DASHBOARD_STATE_DIR=/var/lib/messenger-dashboard
DASHBOARD_KEY_FILE=/etc/messenger-dashboard/master.key
DASHBOARD_STATUS_DIR=/var/lib/messenger-dashboard-status
DASHBOARD_MAX_ACCOUNTS=6
DASHBOARD_LOGGER_IMAGE=messenger-logger:modern
PYTHONDONTWRITEBYTECODE=1
EOF
chown root:messenger-dashboard /etc/messenger-dashboard/service.env
chmod 640 /etc/messenger-dashboard/service.env
install -m 755 "$LOGGER_SOURCE_DIR/server/dashboard-admin" /usr/local/bin/messenger-dashboard-admin
messenger-dashboard-admin init
chown root:messenger-dashboard /etc/messenger-dashboard/master.key
chmod 640 /etc/messenger-dashboard/master.key
chown -R messenger-dashboard:messenger-dashboard /var/lib/messenger-dashboard
if [[ ! -f /etc/messenger-dashboard/tls.crt || ! -f /etc/messenger-dashboard/tls.key ]]; then
  messenger-dashboard-admin certificate --host "$LOGGER_DASHBOARD_HOST"
fi
chown root:messenger-dashboard /etc/messenger-dashboard/tls.key /etc/messenger-dashboard/tls.crt
chmod 640 /etc/messenger-dashboard/tls.key
chmod 644 /etc/messenger-dashboard/tls.crt
for LOGGER_DASHBOARD_UNIT in messenger-dashboard-web messenger-dashboard-bot messenger-dashboard-manager messenger-dashboard-browser; do
  install -m 644 "$LOGGER_SOURCE_DIR/server/$LOGGER_DASHBOARD_UNIT.service" "/etc/systemd/system/$LOGGER_DASHBOARD_UNIT.service"
done
systemctl daemon-reload
systemctl enable --now messenger-dashboard-browser messenger-dashboard-web messenger-dashboard-bot messenger-dashboard-manager
echo "Dashboard: https://$LOGGER_DASHBOARD_HOST:8443"
echo 'Setup links: create, review, and disable invitations in the dashboard. One isolated browser runs at a time.'
echo 'Allow inbound TCP 8443 in Oracle networking and the host firewall.'
echo 'The initial HTTPS certificate is self-signed. Verify this SHA-256 fingerprint before trusting it:'
openssl x509 -in /etc/messenger-dashboard/tls.crt -noout -fingerprint -sha256
echo 'The original messenger-logger service is unchanged. Import it explicitly after checking the dashboard.'
