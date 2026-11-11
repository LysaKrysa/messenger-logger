# Admin dashboard and one shared Telegram bot

Offline alerts and one-time session refreshes are available through the shared
bot. Reconnect links require confirmation by the already paired Telegram owner
and accept only the original Facebook account. They update the existing record
without adding an account, changing pairing or bypassing administrator approval.
Active reconnects appear in **Setup links**, where administrators can disable
them. See [offline alerts and reconnect security](TELEGRAM.md#offline-alerts-and-account-reconnects).

[← README](../README.md#admin-dashboard--one-shared-bot)

Manage a small group from an administrator-only HTTPS dashboard. The initial
limit is **six Messenger accounts in total**, including your own. Every account
has a separate worker, session/device state, archive, media directory, and
Telegram destination. This is an invitation-only deployment for trusted users.

New attachment and voice-file notifications from the shared bot include a
**Download file** button (or **Download ZIP** for bundles). Pressing it requests
a file copy in the same private topic. See [download controls](TELEGRAM.md#download-buttons-in-the-shared-bot).

The **Usage** tab shows live server resources, observed monthly transfer and
editable Oracle Always Free budgets. See [usage and quota tracking](USAGE.md) for
setup, scope, current reference limits and monitoring behavior.

The responsive workspace uses a mobile navigation drawer, grouped summaries and
44 px controls. See [interface specification](UI_DESIGN.md) for layout and tokens.

Each account page also links to **Conversation filters** and **Delivery history**.
Set included/excluded chats and groups separately from forwarding preferences,
inspect sent/failed/uncertain outcomes, and review an explicit resend with a
duplicate acknowledgement. Owners have the same controls through private bot
menus. See [filters](TELEGRAM.md#conversation-filters),
[resend behavior and cache limits](TELEGRAM.md#delivery-history-and-explicit-resends),
and [the upgrade commands](TELEGRAM.md#upgrade-an-existing-dashboard-for-filters-and-history).

## Private user dashboards

Every approved, Telegram-paired owner can open a personal dashboard on the same
HTTPS server. They do not need or receive the administrator password. The public
entry point is `/me/login`; the administrator login also links to it.

1. Send `/panel` (Polish) or `/dashboard` (English) in the paired private bot chat.
   The public entry page can open the same bot with `start=dashboard`.
2. Open the single-use link within ten minutes and request sign-in confirmation.
3. Compare the six-digit code in the browser with the bot's confirmation. Approve
   only your own request, with matching codes, in the existing private Telegram
   chat. Reject unexpected requests.
4. Return to the browser, verify the displayed account/Telegram identity, and
   press **Continue to my dashboard**.

The profile provides:

| Page | Owner controls and information |
| --- | --- |
| Overview | Messenger connection, regular/encrypted transports, capture state, last event/check, retained message/deletion/media counts, current worker-session counts and owned Telegram delivery totals. |
| Settings | Forwarding mode, downloads, immediate available view-once alerts, quiet mode and capture pause/resume. Changes affect only this account. |
| Profile | Display name, Polish/English dashboard and bot language, fixed Messenger/Telegram identity, privacy notice and sign out all browsers. The administrator's account label is unchanged. |
| Conversations | Include/exclude chats and groups, default capture policy and independent forwarding overrides. |
| Delivery history | Retained sent/failed/uncertain results and explicitly acknowledged resends, with the existing duplicate warnings and original expiry. |

The overview can send an eligible one-time reconnect link to the same paired
Telegram chat. The existing offline gate, owner confirmation and original
Facebook identity remain mandatory; it cannot provision another account.

Statistics contain counters and connection booleans only, reported approximately
every 30 seconds. They count incoming captures already retained within 72 hours,
plus separately labelled current-run counts. Filtered/own/out-of-window content
is excluded. Retained counts can include captures from an earlier run; no old
Messenger history is fetched. Paused/offline workers cannot update their
snapshot, so stale reports are labelled and snapshots older than 72 hours are
hidden. A missing report is shown as unavailable, not as zero. Delivery totals
come from that owner's retained history, including explicit resends, not read
receipts, command replies or skipped notifications. Statistics are not server
resource/free-tier budgets; those remain administrator-only under Usage.

Links are hashed in the database, consumed once and bound to the already
approved account/Telegram pairing. A browser claim cannot complete before a
signed, private, bot-authored confirmation from that owner. Reissuing a link
invalidates earlier pending attempts; at most six links per 15 minutes are
allowed per owner. Confirmation sends are claimed durably before network calls;
an uncertain send is not replayed. Ask the bot for a new link if one does not
arrive. No token is placed in a query string or browser storage.

Portal cookies are separate from administrator and setup cookies, scoped to the
HTTPS host, Secure, HttpOnly and SameSite=Strict. Authenticated state-changing requests also
require the portal session's CSRF token and configured Origin. Browser claims
require the one-time link secret and Origin; completion uses a separate claim
CSRF token. Sessions expire
after 30 minutes without requests or eight hours total, with at most ten retained
browser sessions per account. An open auto-refreshing overview sends requests;
the eight-hour absolute limit still applies. Owners can sign out one or all
browsers. Administrators can use **Revoke user sign-ins** on an account page.
These actions do not stop capture. Pairing changes/removal revoke user sessions,
pending logins and profile ownership; pause, restart and same-owner cookie
refresh preserve access. Portal routes never accept an account selector from
the user. Message bodies are available only in that owner's retained-message
viewer. Portal routes exclude cookies, media keys, other owners' data, shared-bot configuration, invitations or host usage controls.

### Upgrade an existing installation

Run this from the extracted release source on the server. The update rebuilds
the temporary browser image and checks that the installed logger image supports
Instagram before stopping the dashboard.

```bash
(
set -euo pipefail
test -f Dockerfile
logger_source_dir=$(pwd -P)
logger_restart_workers=true
if ! sudo docker run --pull=never --rm --network none \
    --mount "type=bind,source=$logger_source_dir,target=/app,readonly" \
    messenger-logger:modern --self-test; then
  sudo docker build --tag messenger-logger:modern .
  sudo docker run --rm --network none messenger-logger:modern --self-test
  logger_restart_workers=true
fi
sudo docker build --tag messenger-onboarding:browser browser/
sudo python3 browser/check_container.py

sudo systemctl stop messenger-dashboard-web messenger-dashboard-bot messenger-dashboard-manager messenger-dashboard-browser
sudo install --backup=numbered -m 644 \
  bridge_rpc.py capture_monitor.py install_bridge.py logger.py conversation_filters.py event_store.py \
  media_saver.py telegram_relay.py telegram_i18n.py telegram_topics.py \
  /opt/messenger-dashboard/app/
sudo install --backup=numbered -m 644 dashboard/*.py /opt/messenger-dashboard/app/dashboard/
sudo install --backup=numbered -m 644 dashboard/templates/*.html /opt/messenger-dashboard/app/dashboard/templates/
sudo install --backup=numbered -m 644 dashboard/static/* /opt/messenger-dashboard/app/dashboard/static/
sudo install --backup=numbered -m 644 browser/*.py browser/seccomp.json /opt/messenger-dashboard/app/browser/
if "$logger_restart_workers"; then
  sudo messenger-dashboard-admin restart-active
fi
sudo systemctl start messenger-dashboard-browser messenger-dashboard-bot messenger-dashboard-web messenger-dashboard-manager
)
```

If the logger image needed rebuilding, the active workers reconnect with the
new image. Use a trusted HTTPS origin for links shared with other users.

### Connect Instagram from Telegram

1. Send `/instagram` to the shared bot in your private chat after your
   Messenger pairing has been approved. The link expires in 15 minutes and
   reserves one account slot. A newer link replaces your earlier request.
2. Open the link yourself and read the consent. Press **Continue**, then
   compare the six-digit code on the page with the bot's message. Approve in
   that same Telegram chat to unlock the browser.
3. Choose **Open Instagram** and sign in to your own account on the actual
   Instagram site. Complete any verification, then open Instagram Home or
   your inbox. Confirm the session and choose **Connect & continue**. The
   browser closes and the Instagram worker starts for your existing chat.
4. Check the connection with `/status instagram` or `/stan instagram`, or
   open the private profile with `/panel instagram`.

Each approved Telegram chat gets one Instagram connection. If you disconnect a
connection made by `/instagram`, the existing account keeps its slot until an
administrator reapproves or removes it. Administrators can review and disable
outstanding links under **Setup links** and manage connected accounts under
**Overview**. Link issuance is limited to three per Telegram user per hour.

### Instagram pilot and manual setup

1. Sign in to the test account at `instagram.com` in your own browser. Open
   Developer Tools → Application → Cookies → `https://www.instagram.com`.
   In the administrator dashboard, choose **Connect Instagram**. Paste cookie
   JSON with `ds_user_id`, `sessionid`, and `csrftoken` from that browser
   session, or paste the cookie export array. Save it through the HTTPS
   dashboard and keep the cookie values out of Telegram and support chats.
2. Choose **Create invitation** and open it in your existing private Telegram
   chat. Reopen the Instagram account from **All accounts**, then choose
   **Approve pairing** and **Start**. Each Telegram owner can pair one Instagram
   account and one Messenger account.
3. Wait for **Connected**. Have a second account send a fresh text DM and
   unsend it. With **Deleted messages only**, the unsend should deliver one
   notification in an **Instagram ·** topic.
4. Enable **Forward available view-once content immediately** and wait for
   **Connected** again. Ask the sender for a fresh view-once photo and video.
   Check Telegram before opening either in Instagram. When the live event has
   a usable attachment, the logger can save and forward it. An unavailable
   attachment is recorded as such. View-once capture is best effort.

Use `/panel instagram` for the private profile and `/status instagram` for
its health. Add `instagram` to other account commands to target that
connection, such as `/mode all instagram` or `/disconnect instagram`.
Account commands without a platform select Messenger when both are paired.
`/disconnect` disconnects both. If an Instagram session expires, an
administrator can update it in **Instagram session**.

## 1. Install alongside the current logger

Use Ubuntu with systemd, Docker, and the existing pinned image
`messenger-logger:modern`. Obtain this release's source on the server, then run
from its root:

```bash
sudo bash server/install-dashboard.sh --host YOUR_PUBLIC_IP_OR_HOSTNAME
```

The installer verifies the existing native image with an offline smoke test,
installs an isolated Python virtual environment, copies public application code
to `/opt/messenger-dashboard/app`, and prompts for an administrator password
of at least 14 characters. No Facebook login, Telegram send, or native rebuild
is attempted during installation. The original logger continues running.

The dashboard URL is `https://YOUR_PUBLIC_IP_OR_HOSTNAME:8443`.

## 2. Permit HTTPS access

In Oracle Cloud, open the instance's VNIC/subnet security list or attached NSG
and add a **stateful TCP ingress rule for destination port 8443**. Restrict the
source range to your admin network when practical. With a changing phone IP,
a wider source range requires the dashboard's strong administrator password.

Allow the same port in the host firewall. If UFW is active:

```bash
sudo ufw allow 8443/tcp
```

On Oracle Ubuntu images using iptables directly, first inspect the existing
rules. Add an allow rule ahead of the final reject rule if required:

```bash
sudo iptables -I INPUT -p tcp --dport 8443 -j ACCEPT
```

Persist host rules using the firewall manager already configured on your VM.
The installer does not rewrite your firewall. **Do not expose port 8788**;
the Telegram gateway binds only to `127.0.0.1`.

## 3. Verify the initial certificate and sign in

The default certificate is self-signed so the dashboard can work with a public
IP without purchasing a domain. Browsers display a certificate warning. Before
trusting it, inspect the certificate's SHA-256 fingerprint and compare it with
the installer output obtained through your trusted SSH connection:

```bash
sudo openssl x509 -in /etc/messenger-dashboard/tls.crt -noout -fingerprint -sha256
```

Do not submit credentials if the fingerprints differ. The browser-specific
certificate details/trust controls vary. The initial certificate lasts one year.
For normal browser trust, use your own hostname and a certificate issued by a
trusted CA, replacing `tls.crt` and `tls.key` in that directory. Keep the key
owned by `root:messenger-dashboard`, mode `640`, and restart the web service.
Keep `DASHBOARD_ORIGIN` in `service.env` consistent with the HTTPS URL you use;
it controls allowed Host and Origin checks. Changing the hostname requires a
matching certificate. There is no HTTP dashboard listener.

Sign in with the administrator password. Sessions expire after 30 minutes
without requests or eight hours total. Password reset revokes all sessions.

## 4. Import your existing account (optional)

For an existing single-account **systemd** installation:

```bash
sudo messenger-dashboard-admin import-existing \
  --env-file /home/ubuntu/messenger.env \
  --data-dir /home/ubuntu/messenger-data \
  --name "My account"
```

This makes read-only bot/chat verification calls, imports the bot configuration,
stops the original `messenger-logger` before copying its archive and encryption
device state, and disables that original logger service. The managed account
uses the existing private Telegram destination; it does not need a new invite.
If copying fails, the importer stops the new account and restores the original
service when it was previously running.

The original directory and `messenger-retention.timer` remain intact, allowing
the old archive to expire normally after 72 hours and allowing rollback.
The original `messenger.env` remains private, plaintext configuration; remove
or secure any obsolete credential backups yourself once migration is verified.
The importer preserves forwarding mode and the view-once forwarding preference.
Managed workers otherwise use the dashboard defaults: encrypted transport,
Europe/Warsaw time, 25 MB local media-download limit, and 512 MiB worker RAM.

Every managed account captures incoming messages only. The account owner's own
messages, media, and quoted text are excluded. Telegram deletion notices keep
details and downloadable files together in one message; multiple attachments
are delivered as one ZIP. Voice messages are downloadable documents.

Existing topic mappings/device state are copied. Only newly observed events
are captured after startup; installing/importing does not replay failed sends
or earlier message history.

## 5. Connect the bot and invite friends

If you imported your account, its existing bot is already configured. Otherwise,
open **Telegram bot**, enter your BotFather token, and choose **Verify & save**.
Enable **Threaded mode** for that bot in BotFather first. The token is encrypted
and never displayed again. A replacement token must belong to the same bot.

For each friend:

1. Choose **Add account** and enter an identifying label.
2. Paste cookie JSON supplied by the account owner, either now or later. A
   name/value object or browser-export array is supported. Do not send cookies
   through Telegram or GitHub.
3. Choose **Create invitation**. Share the single-use link with that person.
4. They open it in their private bot chat and press **Start** within 15 minutes.
5. Reload the account page and verify the displayed Telegram username/user ID
   with the intended person, then choose **Approve pairing**.
6. Choose **Start** once cookies and pairing are saved. The page updates every
   15 seconds when no form is being edited. Wait for **Connected**.
7. With the default **Deleted messages only** mode, ask another person to send
   a fresh Messenger test message, wait for capture, then unsend it. The logged-in
   account's own messages are excluded.

One Telegram user can be paired to one Messenger account. Each private chat
has its own Messenger conversation topics. The gateway checks the destination
on every text, media, and topic request. Pairing does not grant dashboard access.
Regenerating an invitation revokes its predecessor. Disconnecting a pairing
immediately revokes the worker gateway credential and requests worker shutdown.

The bot defaults to Polish. `/en` switches only the requesting Telegram user to
English across their topics; `/pl` switches that person back. Preferences persist
without restarting workers. Captured text, names and files keep their original
content. See [language settings and upgrade steps](TELEGRAM.md#language-per-user).

The bot also supports `/help`, `/privacy`, and `/disconnect` (`/stop` is an alias).
Owner-only `/status`, `/settings`, `/pause`, `/resume`, `/mode`, `/media` and
`/quiet` controls have Polish equivalents and localized replies. A welcome notice
points to `/help` on first contact. See [the command reference](TELEGRAM.md#private-bot-commands)
for examples, quiet-mode behavior and status freshness.
Disconnect only affects the requesting user's account. Invitation messages and
arbitrary incoming bot messages are not added to Messenger archives.

## Administrator controls

To troubleshoot a user's saved session, open their account, expand **Messenger
session**, then click **Edit saved cookies**. This explicitly loads the stored
cookie JSON into an administrator-only editor. Edit it and choose **Save cookie
changes**. **Reload saved cookies** discards the current input and loads the latest
stored session; **Cancel** returns to the account without saving.

Cookies remain encrypted in the database. The editor response is private and
non-cached, and credentials are cleared from its fields when the document is
left. Ordinary account pages and user dashboards do not display stored cookie
values. The editor requires the existing administrator session and CSRF/Origin
checks. Invalid JSON and a different `c_user` are rejected; if another reconnect
or administrator refreshes the session meanwhile, reload before saving. Saving
rotates the worker credential/revision and reconnects an active account; a paused
account stays paused. User profile sessions and Telegram pairing are preserved.

For this update, copy `dashboard/*.py`, `dashboard/templates/*.html` and
`dashboard/static/*` from the extracted release to their corresponding directories
under `/opt/messenger-dashboard/app/`, then restart the three dashboard services.
No account-worker restart or native rebuild is needed just to expose the editor.

| Control | Behavior |
| :--- | :--- |
| Save cookies | Validates required fields and account identity, encrypts the new credentials, and restarts an active worker. |
| Start / Restart / Stop | Changes only that account; the manager applies the request on its next pass. |
| Forwarding preferences | Deleted-only or all supported chat content, plus opt-in immediate forwarding of available view-once content. |
| Disconnect pairing | Stops the worker and removes its Telegram binding; existing Telegram messages remain. |
| Remove account | Revokes its key immediately; the manager stops its worker and removes that managed local archive/configuration. Existing Telegram copies and manually retained legacy backups remain. |

Read receipts, typing, presence, and transport controls remain excluded. Media
downloads and three-day retention retain the standalone logger's behavior.
The manager runs credential-free cleanup for stopped accounts too.

## Services and files

| Location/service | Purpose |
| :--- | :--- |
| `messenger-dashboard-web` | Unprivileged HTTPS administrator UI, port 8443 |
| `messenger-dashboard-bot` | Unprivileged pairing receiver and shared outbound gateway, loopback port 8788 |
| `messenger-dashboard-manager` | Root-only container lifecycle and archive cleanup |
| `/var/lib/messenger-dashboard/dashboard.sqlite3` | Account settings, hashed invitations/sessions, encrypted credentials |
| `/etc/messenger-dashboard/master.key` | Credential encryption/signing key; keep private and back it up securely |
| `/var/lib/messenger-accounts/ACCOUNT_ID/data` | Separate local archive, media, and device state |
| `/run/messenger-dashboard-workers/ACCOUNT_ID.json` | Root-only temporary worker secrets; recreated after reboot |
| `/var/lib/messenger-dashboard-status` | Fixed state/counters for the dashboard; no message bodies |

The web and bot users are not members of the Docker group and cannot access
the Docker socket. The root manager executes fixed argument lists, validates
generated account IDs, and mounts a separate archive per worker. Workers do not
receive the shared Telegram bot token. Their gateway keys cannot address another
user's chat or invoke destructive Telegram methods. Plaintext account cookies
are mounted from `/run`, not stored in Docker environment metadata.

Workers use host networking to reach the loopback gateway. These containers
provide separate storage and credentials, not a hardened boundary for hostile
tenants. The host administrator can access every account. Encrypted credential
storage does not encrypt message/media archives or protect against a compromised
administrator/host. Backups need both the configuration database and master key;
back up private archives separately if desired, accounting for retention.

One receiver must own this bot's `getUpdates` stream. The dashboard detects an
existing webhook and reports it instead of deleting it. Resolve another bot
poller/webhook deliberately before using invitations. Outbound sends are paced
centrally and every account has a request limit. Ambiguous delivery outcomes
are not automatically resent. The existing topic-missing repair remains active.

## Telegram media uploads

An older shared gateway rejected normal image/file multipart requests at about
32 KiB before contacting Telegram, then reported the parser exception as HTTP 502.
This did not affect local media saving. The corrected gateway allows a bounded
multipart decoder window while retaining the 32 KiB text-field limit, file and
body limits, part count, destination checks and no ambiguous automatic resend.
Parser/size errors retain their real HTTP 4xx status.

For this fix, install `dashboard/broker.py` from the updated revision into
`/opt/messenger-dashboard/app/dashboard/broker.py`, keeping a backup, and run
`sudo systemctl restart messenger-dashboard-bot`. Existing logger workers can
keep running. Test a fresh incoming attachment after the gateway is ready.
Previously failed sends are not automatically replayed; saved files remain
available locally until their normal 72-hour retention expires.

## One-time setup links

Open **Setup links** and choose **Generate setup link**. A label is optional.
Pick a 1-, 6-, or 24-hour lifetime, copy the link shown once, and share it privately.
An unused invitation reserves one account slot. Opening a link or chat preview
does not consume it; pressing Continue claims it for that visitor's browser.

Your friend enters their name, reads the session-access explanation, and opens
the real Facebook website in a temporary browser hosted on your server. They
complete Facebook login and verification themselves, then explicitly approve
connecting that session. The server encrypts the required cookies, creates their
isolated account, closes the temporary browser, and shows their personal Telegram
invitation. You do not copy their cookies or type their account details. Approve
the resulting Telegram identity and press **Start** on the account page as usual.

The invitation list shows active links and recent history: Ready to share, Setup
in progress, Completed, Disabled, and Expired. **Disable link** blocks further
guest requests immediately and asks the controller to close that browser. It
also revokes any unused Telegram invitation from a completed setup; it does not
stop an approved account. Use the account controls to stop or remove one.

Claimed sessions have a hard **20-minute deadline**, and cannot be reused from
another browser. Telegram invitations last 15 minutes. If setup expires, generate
a new link. Existing approved accounts and the 72-hour retention are unchanged.

### Browser installation and operation

Rerun `server/install-dashboard.sh --host YOUR_PUBLIC_HOST` from this release.
It builds a separate `messenger-onboarding:browser` image and runs an offline
sandbox/input/frame test **before stopping a working dashboard**. This image
uses Debian Chromium for the host architecture, including Oracle ARM64. The
Messenger native transport is not rebuilt. A failed sandbox check blocks the
upgrade; the installer does not disable Chromium's sandbox as a fallback.

The new `messenger-dashboard-browser` service listens only on `127.0.0.1:8789`.
No additional public port, VNC port, or Chrome debugging port is needed. Web and
Telegram services retain no Docker access; the separate controller has a private,
authenticated API limited to fixed operations and a fixed browser image.

Each browser container runs as an unprivileged user, with Chromium's sandbox,
a read-only root filesystem, no capabilities, no logger/database/bot-token
mounts, no Docker socket, no persistent profile, and no Docker logs. Its IP
network is disabled. Its only egress is a mounted Unix-socket CONNECT proxy
restricted to HTTPS Facebook/Messenger/CDN domains, the exact documented
Google reCAPTCHA hosts, and four Arkose client hosts: `meta-api.arkoselabs.com`,
`client-api.arkoselabs.com`, `iframe.arkoselabs.com` and `cdn.arkoselabs.com`.
Unlisted Arkose hosts stay blocked. The browser additionally limits Google requests to `/recaptcha/`
paths; general Google pages and unlisted Google subdomains remain blocked.
Arkose's API, embedded challenge and CDN use different paths on those exact
hosts. The browser and proxy use the same host policy. DNS answers must be public
IPs, and connections are pinned to the checked addresses. TLS passes through
without decryption. The browser cannot directly reach the host, other containers,
or cloud metadata. Passwords, input events, screenshots, and verification codes
are not recorded. Temporary files live in memory and disappear when it closes.
Only the confirmed session cookies are saved using the existing encryption key.

The remote view uses a live JPEG stream capped at 10 frames per second, rather
than a screenshot reload every 1.2 seconds. Direct compositor capture avoids
font-loading waits for each frame. Typing and wheel bursts are batched in order,
and touch swipes scroll the remote page. Inputs with an unknown delivery outcome
are never automatically replayed. The view pauses while its tab is hidden and
resumes on return; a reduced-rate snapshot fallback supports interrupted streams.
No frames are recorded. Revocation, completion and expiry close an open stream,
and stream concurrency is bounded so inputs and administrator requests retain
available web threads.

Clicks and scrolling use the decoded frame's intrinsic dimensions, so the
display can shrink on mobile or change with page zoom without moving the remote
click target. An older viewer used the CSS display dimensions for both sides
of this conversion, causing misplaced clicks when the image was scaled.

On phones and coarse-pointer devices, the large local typing box is collapsed.
Tap the desired field in the remote view and type; **Keyboard** can refocus the
phone keyboard, **Enter** submits to the remote page, and **Done** dismisses it.
**Typing box** reveals an ordinary local input for manual paste/accessibility.
The default input stays focusable but visually transparent, positioned inside
the visual viewport rather than below the remote image. Prevent-scroll focus
and a brief focus guard avoid jumping to that old box; a new gesture or blur
cancels the guard so the page remains scrollable. The operating system controls
its keyboard and viewport resizing, so test on the intended phones.
Local autocorrection is disabled, composition commits once before sending text,
and intermediate IME Enter/deletion events are not forwarded. Input remains
transient; switching invitations, completing setup or cancelling clears it.

During **Connect & continue**, the viewer pauses and the confirmation button
stays disabled. The page checks saved setup progress even while the browser
is active, and moves to Telegram once confirmation is committed, including
when the original response is lost or browser cleanup is still finishing.
Login clicks and confirmation are not automatically resent after an ambiguous
response. An earlier input error can be cleared for handoff only after a fresh
read-only readiness check; consent and server-side confirmation still apply.
Transient screenshot target failures discard that capture session so a later
frame can recover. Repeated browser metadata failures show **Open Facebook**
again, and completed setup is recovered independently. If Chromium exits before
the connection was confirmed and saved, reopen the browser and sign in again.

Facebook can show Google reCAPTCHA or Arkose Labs MatchKey for verification;
the provider named in the Meta notice can differ between sessions. Older
browser releases blocked these dependencies, leaving the notice with no challenge.
Update with the installer above, then reopen the temporary browser to load them.
Complete the challenge yourself. The offline check verifies synthetic scripts,
embedded frames, an API request and images across both providers' domains through
the private proxy; it does not contact either provider or solve a live CAPTCHA.
The policies follow [Google's reCAPTCHA FAQ](https://docs.cloud.google.com/recaptcha/docs/faq)
and [Arkose's Domain Policy](https://developer.arkoselabs.com/docs/domain-policy).
Passing these offline checks confirms dependency routing, not that a provider
will accept a real remote login. If the live challenge is still blank, tell the
administrator which provider the notice names and whether reloading changes it.

**One browser runs at a time**, with a 1 GiB RAM limit. Other invitees can retry
after the current person finishes. Independent expiry cleanup closes abandoned
sessions even if the web service is down. On restart, the controller removes
orphaned containers carrying its exact managed label, leaving logger containers
alone.

```bash
sudo systemctl status messenger-dashboard-browser --no-pager
sudo journalctl -u messenger-dashboard-browser -n 40 --no-pager
```

The website is visibly labelled as a remote browser, without an imitation
Facebook form or fake address bar. Facebook may require extra checks or reject
a remote browser. Verification is never bypassed. Device-bound passkeys,
hardware security keys, cameras and microphones are not forwarded; administrator
cookie import remains available when remote login is unsupported.

For sharing with friends, use a DNS hostname and trusted HTTPS certificate.
The existing self-signed certificate works for testing but shows a browser trust
warning. Follow the TLS instructions above to replace it. The service operator
can access the confirmed Facebook session; guest consent screens state this.

## Operations and recovery

To update an existing dashboard, run the installer from the desired source
release with the same hostname. It preserves the master key, password, and
configuration database. Restart each managed account from its dashboard page
afterwards so its Python process loads the updated code. Existing certificates
are preserved; renew or replace them separately when necessary.

```bash
sudo systemctl status messenger-dashboard-web messenger-dashboard-bot messenger-dashboard-manager --no-pager
sudo journalctl -u messenger-dashboard-manager -n 40 --no-pager
sudo messenger-dashboard-admin password
```

For an individual managed worker, obtain its generated account ID from the
dashboard page URL, then use `docker stats --no-stream messenger-account-ID`.
Do not share Docker inspect dumps, cookie files, full archives, or private logs.

On configuration/authentication exit 78, the manager waits for corrected cookies
or an explicit Restart. Other exits have a one-minute delay and a five-start
limit per 15 minutes. It does not continuously retry failed Facebook logins.
Restarting the manager never replays earlier Telegram attempt records.

To roll back the imported original account, first **Stop** its managed account
in the dashboard and wait for **Stopped**, then:

```bash
sudo systemctl enable --now messenger-logger
```

That uses the original directory/configuration; messages captured only in the
new managed directory are not automatically copied back. Do not run both copies
for the same Messenger account. To disable all new services, stop the manager
and remove only its labelled account containers after a graceful stop; stopping
the web UI alone does not stop account workers.

## Development and validation

```bash
python3 -m pip install -r requirements-dashboard.txt
python3 -m unittest discover -s dashboard/tests -v
python3 -m unittest discover -s tests -v
```

Dashboard tests use synthetic identities, mock Telegram responses, temporary
archives, and a recording Docker adapter. They exercise full existing relay
instances through the destination-checking gateway. They make no real Telegram
sends or Facebook logins. Live deployment still requires verifying firewall,
certificate trust, your sessions, and the real Telegram destination.


## Capture health and private message viewer

Administrators can open **Capture health** in the sidebar for an account overview,
or open an account's health page. User profiles have their own **Capture health**
page and **Message viewer**. User routes resolve the account from the approved
portal session. The administrator health pages expose diagnostic metadata.

Health reports distinguish connection status, successful incoming capture,
encryption/unavailable-event warnings, failed media downloads, queue skips and
Telegram delivery outcomes. Warning counts include retries and can include events
without identifiable incoming content. They are not counts of unique lost messages.
Reports older than 90 seconds are shown as stale. Idle conversations alone do not
trigger warnings.

Capture alerts default to enabled for each pairing. Users can disable them on
the health page. Three encryption or media warnings within ten minutes, or one
queue skip, trigger an owner-only alert. Each category has a six-hour cooldown.
The existing user's Polish/English preference applies. Capture alerts bypass
quiet mode. A recovery notice requires a subsequent incoming capture and ten
minutes without new warnings. Earlier content is not assumed recovered.

The message viewer lists conversations separately and searches within the
selected conversation by text or filename, sender name, date, attachment type,
and all/deleted/undeleted status. It includes original text for captured edits.
Web previews show up to 20,000 characters per text version and 100 attachment
entries. A visible notice identifies shortened previews. Search covers those
previews; complete captured content remains in the local archive.
Attachment entries show metadata and download state. Saved files remain available
through Telegram's existing download controls.

Workers synchronize encrypted display copies through their authenticated local
gateway. The dashboard service does not gain access to Docker or worker directories.
Copies expire no later than 72 hours after message creation/capture, and editing,
unsending or resynchronizing cannot extend expiry. The bot removes expired copies
in its regular maintenance loop. Each web query independently excludes expired data.
Re-pairing removes prior copies and rejects older captures. Cookies, media URLs,
media keys, device state and local paths are excluded from viewer records.

Updated active workers synchronize their existing retained incoming records, then
report later edits, deletions and media states. This reads the local archive and
never fetches old provider history. A paused worker resumes synchronization when
started. Filters are applied to captured content; exclusions prevent future capture.
The viewer does not reveal the logged-in account's outgoing messages.

Unsupported legacy records (for example, records without an original creation
time or with malformed metadata) are skipped during web synchronization without
changing their local originals. They cannot block newer valid copies. Operational
logs report skipped records at most once per five minutes and sync failures at
most once per minute, using fixed categories without message content. A subsequent
accepted batch records recovery. Dashboard-copy retries do not resend Telegram
notifications and do not repair unavailable or undecryptable provider events.

### Diagnose a connected account that captures no new messages

Worker logs contain two aggregate summaries per minute. **Capture intake** counts
supported message events received by the Python adapter and the admission stage
that rejected them: timestamp checks, capture policy, own/unattributed sender,
duplicate detection, or successful storage. `controls_ready=false` means the
worker has not obtained a valid conversation policy from the gateway.

**Native crypto diagnostics** classifies recognized warning patterns in the pinned
native libraries. Categories distinguish unavailable ciphertext, explicitly
unavailable view-once events, missing sessions, missing sender keys, missing
prekeys, identity/signature warnings, MAC errors and other decryption errors.
Counts can include retries, own-device events and unrelated traffic. They cannot
identify the number of unique missed incoming messages or prove which warning
belongs to a particular test message. `stderr_lines` includes unrelated native
warnings. Unrecognized text is discarded, and oversized lines are discarded in
full. No native warning text, message bodies, identifiers, cookies or keys are
written to these summaries.

Read the latest summaries for an account with:

```bash
sudo docker logs --since 5m YOUR_ACCOUNT_CONTAINER 2>&1 |
  grep -E 'Capture intake:|Native crypto diagnostics:' | tail -n 2
```

These diagnostics do not reset encryption state, alter the future-only capture
cutoff, restore missing ciphertext or automatically resend Telegram notifications.
