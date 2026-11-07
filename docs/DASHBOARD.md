# Admin dashboard and one shared Telegram bot

[← README](../README.md#admin-dashboard--one-shared-bot)

Manage a small group from an administrator-only HTTPS dashboard. The initial
limit is **six Messenger accounts in total**, including your own. Every account
has a separate worker, session/device state, archive, media directory, and
Telegram destination. This is an invitation-only deployment for trusted users.

The responsive workspace uses a mobile navigation drawer, grouped summaries and
44 px controls. See [interface specification](UI_DESIGN.md) for layout and tokens.

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

The bot supports `/help`, `/privacy`, and `/disconnect` (`/stop` is an alias).
Disconnect only affects the requesting user's account. Invitation messages and
arbitrary incoming bot messages are not added to Messenger archives.

## Administrator controls

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
