# Validation

## Bilingual private bot controls

- Command flows cover first-contact and invitation welcomes, both help languages,
  Polish aliases/arguments, per-user persistent settings, idempotent pause/resume,
  and rejection of group, mismatched-sender, other-bot, malformed, unpaired and
  pending-approval requests. Duplicate/uncertain updates are not replayed.
- Pause/resume rotate worker credentials on actual state changes; a stale worker
  key stays revoked after resume while the new worker uses its new key.
- Real Flask gateway integration checks live mode/media changes without worker
  revision changes, continued local quiet captures, delayed media suppression,
  early quiet cancellation/expiry and a quiet change between settings and send.
- Media-worker tests cancel queued/new downloads without a bridge call, then
  verify exact future file bytes after enabling downloads again.
- Worker reports have strict fields, bounded timestamps and revision checks;
  stale connections become unknown. Delivery outcomes reflect actual relay API
  results and retain last confirmed send across an uncertain outcome.
- New approved recipients cannot inherit a previous recipient's control or
  health metadata. Tests use synthetic users/files and fake Telegram responses;
  no real Facebook login or Telegram send occurs.

## Per-user Telegram languages

- Two live relay instances through the real Flask gateway follow only their
  owners' `/en` and `/pl` commands; preferences survive reopening the store and
  disconnects, can be chosen before pairing, and never change account revisions.
- Sender/chat/address/argument checks prevent one user from targeting another.
  Worker locale reads are authenticated, empty-body, owner-scoped and read-only;
  invalid or revoked requests cannot access another user's preference.
- Polish/English formatting checks preserve original text, names, HTML literals,
  emoji UTF-16 entities, edits/reactions, filenames and exact media bytes. ZIPs
  contain localized full details and remain a single notification. Prior-run
  reaction quotes stay excluded. Dates and Polish plural forms are deterministic.
- Upload buttons, download callbacks and requested copies follow the recipient's
  language. Lookup failure retains the worker's last language. The upgrade
  command schedules only desired-running accounts and preserves paused accounts.
- These tests use synthetic identities/files and fake Telegram responses. No
  real Facebook login, message capture or Telegram send is performed.

## Telegram download buttons

- Multipart gateway checks keep exact voice/file/ZIP bytes and captions while
  adding one controlled inline keyboard and forcing document delivery.
- Callback regressions verify private recipient ownership, approved/revoked
  pairings, bot authorship, topic/reply preservation, paused capture, malformed
  documents, URLs/paths, throttling and durable duplicate-update handling.
- Simulated uncertain sends are not automatically replayed after a receiver
  restart. Workers cannot inject keyboards or call callback/file APIs.
- The existing single receiver polls both messages and callbacks. All API calls
  in tests use synthetic data and fake Telegram responses; no real send occurs.

## Server usage and free-tier budgets

- Synthetic Linux counters verify CPU guest-tick handling, memory availability,
  physical/default-route interfaces and exclusion of container/loopback traffic.
- Accounting regressions cover provisioned allocation versus CPU load, changes
  applying forward, restart persistence, counter/boot resets, clock rollback,
  UTC month/year boundaries, small non-zero amounts and missing/stale history.
- File-size scans skip symlinks, are bounded by work/time and publish no bodies
  or credentials. Collector failures leave account management running.
- Administrator route tests verify authentication, CSRF/origin protection, atomic
  finite numeric settings, expiring monthly manual requests and safe rendering.
- Real browser fixtures cover Usage navigation, settings persistence, meters and
  44 px fields, expanded-form overflow and five responsive widths.

## Dashboard redesign

- Real Flask pages are checked at 320, 390, 768, 1024 and 1440 px for horizontal
  overflow, page titles, 44 px controls and 4.5:1 normal-text/button contrast.
- Browser fixtures check mobile navigation focus wrapping/restoration, Escape,
  backdrop dismissal, actual touch navigation, and breakpoint/orientation changes.
- Screenshots cover account, bot, invitations, navigation and sign-in on mobile,
  plus the existing desktop and complete guest setup flow. Long labels wrap.
- Existing credential isolation, CSRF, approval, media upload, click geometry,
  handoff recovery and Chromium/WebKit typing regressions remain in place.

## Mobile keyboard and focus

- JavaScript regressions cover positioning inside a shifted/reduced visual
  viewport, bounded correction of a browser focus jump, cancellation by user
  gestures/blur, disabled inputs and the visible typing fallback.
- Mobile-sized Chromium and WebKit fixtures check a transparent in-viewport
  typing target, stable page position after a tap, reduced viewport positioning,
  intentional scrolling, composition without an early Enter/duplicate text,
  Done, visible paste fallback and no horizontal overflow.
- These are headless fixtures using synthetic text. A physical iOS/Android
  software keyboard is not emulated; opening/dismissing it needs phone retest.

## Shared Telegram media uploads

- Reproduced the old failure with 32 KiB, 64 KiB, 256 KiB and 1 MiB synthetic
  files: HTTP 502 with zero upstream requests. A 16 KiB file succeeded.
- Exact binary upload regressions cover 32 KiB through 5 MiB; text fields,
  file/body size and multipart part-count limits remain enforced as HTTP 413
  before any upstream send. Destination/isolation and ambiguous-outcome tests remain.
- A real loopback HTTP test downloads an encrypted view-once fixture with SHA-256
  validation, saves it privately and forwards its exact bytes once through the
  shared gateway to a fake Telegram API. A repeated callback does not resend.
  No real photos, Facebook session or Telegram message are used.

## Facebook-to-Telegram handoff recovery

- The real HTTPS guest fixture injects a transient viewer error and a failed
  input acknowledgement, delays browser cleanup for six seconds after commit,
  and replaces the finish response with HTTP 503. The UI must show Telegram
  while cleanup is pending, without a refresh, repeated input or duplicate capture.
- Server tests cover capture failure racing with an already committed completion,
  cleanup failure after commit and an unsaved failure staying incomplete.
- Capture tests cover detached targets, changed pages, failed attachment/detach
  and malformed frames. URL tests keep readiness and confirmation aligned with
  the existing login/checkpoint/recovery boundary. No unconfirmed login is saved.

## Verification challenge dependencies

- Shared policy tests cover the documented reCAPTCHA scripts, frames and payloads,
  exact Google hosts and paths, four specific Arkose hosts, TLS/port rules and Facebook-only
  session confirmation. Third-party DNS is still rejected for private/metadata IPs.
- The real sandboxed Chromium check loads synthetic Google and Arkose scripts,
  cross-origin frames, an API POST and images through the private Unix-socket proxy.
  It asserts that unrelated Google paths, unlisted Google subdomains and Arkose
  unlisted Arkose hosts and lookalike domains never reach the fixture server. There are no live provider,
  Facebook or Telegram requests. A real login remains a manual deployment check.

## Remote browser responsiveness

- Geometry tests cover intrinsic frame dimensions versus CSS display size,
  mobile/desktop frames, zoom, page offsets, frame edges and unloaded images.
- The real HTTPS guest flow submits taps/clicks at four viewport/zoom settings,
  then applies the submitted coordinates to small buttons in the source browser
  and checks actual hits without lost or duplicate clicks.
- Live-view tests cover binary JPEG streaming, no-cache/same-origin headers,
  guest authorization, active revocation/expiry, and bounded stream concurrency.
- Input tests cover bounded batches, validation before dispatch, Unicode,
  slow-connection ordering, rapid text/wheel coalescing, no ambiguous resend,
  and clearing pending input across invitation changes.
- The HTTPS guest test verifies multiple frames per second, hidden-tab pause
  and resume, and the existing approval and Telegram handoff.
- The real offline browser check verifies batched input, metadata without images,
  and times direct compositor capture while retaining the sandbox/proxy checks.
- All fixtures remain synthetic; no Facebook login or real Telegram send.

## Self-service setup links

- Synthetic tests cover atomic one-time claims, consent, origin/CSRF checks,
  guest/admin separation, capacity reservations, expiry, disabling links,
  credential-commit revocation races, encryption and Telegram pairing isolation.
- Browser-controller tests cover fixed commands, private authentication, one
  active session, hard expiry, prescribed container flags, and public-IP egress.
- The HTTPS browser flow exercises invitation creation, guest setup, remote input
  with a synthetic image, Telegram handoff, administrator approval, mobile layout,
  one-time replay rejection and revocation of an active browser session.
- A separate real Docker smoke check verifies sandboxed Chromium, synthetic input,
  JPEG frames, no IP network, no published ports, unprivileged execution, read-only
  root, bounded memory and the sole read-only proxy-socket mount.
- No tests log into Facebook or send a real Telegram message. Actual Facebook
  remote-login acceptance and device verification must be checked by the owner.

## Incoming-only capture and single-notice downloads

- 187 standalone tests passed with the pinned offline native RPC check, and
  38 dashboard tests passed locally.
- Authenticated sender admission excludes outgoing regular/encrypted messages,
  attachments, owner actions, and quoted owner text before content persistence.
- Synthetic runtime tests verify incoming-only counters, dispatch, out-of-order
  deletion attribution, and legacy owner-record isolation in views and media.
- Telegram tests check exact voice/document bytes, one-message multipart captions,
  pending download completion, safe ZIP members, full long-text transcripts,
  combined upload limits, ambiguous-send suppression, and topic-repair rewinds.
- Tests use synthetic accounts and mocked network requests; no Facebook login
  or real Telegram send is performed. Native transport inputs are unchanged.

## Optional administrator workspace

- 172 standalone tests pass, including the pinned native offline RPC test and
  mounted-credential/gateway configuration checks.
- 34 optional dashboard tests cover encrypted credentials, account identity,
  expiring invitations, approval, destination isolation, sessions, CSRF,
  migration rollback, independent archives, cleanup, and account removal.
- A real HTTPS browser flow verifies sign-in, account creation, hidden saved
  cookies, invitation/approval/start, and mobile layout without horizontal
  overflow. Screenshots use synthetic accounts only.
- Real Docker fixture workers verify separate identities/data, runtime-mounted
  secrets without credential environment metadata, resource limits, graceful
  shutdown, offline cleanup, and scoped removal. The test also exercises a root
  controller using an unprivileged owner's SQLite WAL files.
- ARM64 binary wheels for the pinned cryptography dependency and CFFI were
  verified available for Ubuntu's Python 3.12; native inputs remain unchanged.

These checks make no Facebook login or real Telegram send. Actual Oracle
deployment still requires the operator to configure ingress, verify certificate
trust, and test their own sessions and approved Telegram destinations.

Offline checks completed on Linux amd64, 5 November 2026.

## Current source tree

- All **151 Python unit/integration checks passed** with the compatible native
  binary supplied through `BRIDGE_TEST_BIN`.
- Python syntax, Bash wrapper/installer syntax, SVG XML, workflow/template YAML,
  and relative Markdown document links were checked.
- Native Go source and pinned binary hashes are unchanged. The Python reader
  additionally filters the pinned Signal logger's exact diagnostic prefix and
  makes JSON/encoding failures restartable. The runtime image includes license
  documents; the hotfix also works through the existing full-source bind mount.
- The source-only publication set excludes private configuration, cookies,
  tokens, messages, media, device state, databases, personal server addresses,
  SSH material, build caches, and compiled transport binaries.
- Git ignore rules cover common runtime data and private configuration paths.
  A clean source history is prepared independently of the old application.

## Python coverage

The suite uses synthetic events/cookies and mocked Telegram requests:

- Cookie shape/domain validation and account matching before event storage.
- Native response/event interleaving, bounded parsing, cancellation, redacted
  diagnostics, receive-only RPC exposure, and reconnect-state handling.
- Future-only admission, duplicate suppression, per-run counters, typed text,
  attachment metadata, replies, mentions, edits, reactions, and observed unsends.
- Receipt/typing/presence exclusion and no raw device/media keys in persisted logs.
- Durable SQLite journal/export recovery, schema upgrades, account isolation,
  read-only views, names/usernames, and placeholder rejection.
- Authenticated/decrypted media, integrity checks, size limits, sanitized/private
  atomic files, failed/interrupted downloads, and native RPC scheduling.
- Telegram formatting, UTF-16 offsets, multipart bytes, deletion/all modes,
  view-once opt-in, topic mapping, destination scoping, permissions, and repair.
- Threaded-mode checks refreshing after cooldown and immediate recheck on restart.
  The diagnostic calls only getMe/getChat/getChatMember and prints no credentials.
- Rolling local expiry of messages, related actions, quoted copies, journal/log
  records, media, old snapshots, stale labels, and attempt records.
- Interrupted cleanup recovery, late-download rejection, old replay rejection,
  physical database/WAL compaction fixtures, and offline cleanup locking.
- Distinct permanent/transient exit codes and no auth attempt for invalid config.

No test sends a Messenger message or a Telegram notice. Most connection tests
use a local synthetic RPC subprocess. The native test calls only credential-free
local RPC methods.

## Reader recovery regression

A synthetic 122-byte Signal diagnostic reproduced the reported parser signature:
JSON syntax failure at column 2 on a line beginning with a bracket. The new
reader recognizes only the pinned DefaultLogger severity/timestamp/Go-caller
prefix and continues to process subsequent events and RPC responses. Only a
fixed severity notice is emitted, once per level; diagnostic text is not retained.

Checks cover all emitted severities, UTC/offset timestamps, event/response
interleaving, preservation of log-like text inside actual messages, and rejection
of unrecognized bracketed output. Unrecognized JSON/encoding failures stop the
reader with retryable exit 1; protocol shape errors remain terminal. Existing
auth/checkpoint handling still exits 78 and does not repeatedly retry login.

## Native checks already completed for this exact source

- Built `2.3.4-logger.1` for Linux amd64 and arm64 with verified Go 1.26.10.
- Passed the native Go suite, including Consumer/Raven view-once wrappers,
  malformed/nil transports, ephemeral labels, metadata, and contact lookup.
- Repeated source preparation from clean pinned checkouts and independently
  reproduced the amd64 binary SHA-256.
- Executed amd64 hello/isConnected/disconnect and contract/hash checks without login.
- Checked the cross-compiled arm64 ELF target. It was not executed in development.

Exact source pins and both hashes are in [THIRD_PARTY.md](THIRD_PARTY.md).
The native source did not change during open-source preparation.

## Not established by these checks

- A successful build of the current Dockerfile: no Docker daemon was available
  in the development environment. CI is configured to build and test it.
- Actual systemd installation, boot recovery, or service/timer behavior on a
  target VM. Development did not have a running systemd manager; unit verification
  could not initialize in the restricted filesystem.
- Live authentication, checkpoints, encrypted registration, full conversation
  coverage, attachment resolution/availability, or actual Telegram delivery.
- Execution of the arm64 binary in the development environment.
- CI results for your exact revision: inspect its GitHub Actions run; local
  results and earlier successful CI runs are separate evidence.

A user must test their own current session and destination after deployment.
The project deliberately distinguishes local/offline checks from live-service
compatibility.
