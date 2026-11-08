# Changelog

## Unreleased — conversation filters and delivery history

- Add per-account default/include/exclude capture policies for chats and groups,
  with independent inherit/all/deleted/off forwarding rules. Gate local capture,
  queued downloads and gateway forwarding; report only conversation IDs/names.
- Add admin account pages and owner-checked `/chats` / `/rozmowy` and
  `/deliveries` / `/wysylki` menus with complete Polish/English copy.
- Persist confirmed sent, definite failed and uncertain notification outcomes.
  Keep resend payloads in a bounded, account-keyed encrypted cache. Resends require
  a single-use acknowledgement, retain original expiry, preserve exact document
  bytes, stay in the original owner's destination and never retry uncertainty.
- Separate pairing generation from worker restarts, retaining owner history
  through pause/reconnect while invalidating it on unlink/reassignment/removal.
  Expire history/cache independently of workers within the existing 72-hour window.

## Unreleased — offline alerts and fixed-account reconnects

- Monitor worker connection reports in the shared bot and send debounced,
  localized offline alerts independently of capture workers. Suppress alerts
  for intentional pause/unlink; avoid duplicate uncertain sends and flapping.
- Add single-use, expiring reconnect links and localized `/reconnect` / `/polacz`
  requests. Require confirmation from the original Telegram owner before opening
  the browser, and accept only the originally approved Facebook account.
- Refresh credentials in the existing record, rotate worker keys, preserve pairing
  and options, and reconnect without provisioning another account or slot.
- Invalidate stale/recovered/reassigned/revoked links, reject different Facebook
  IDs without writing cookies, and expose reconnect activity to administrators.

## Unreleased — help in the selected language

- Show Polish commands, arguments and explanations for Polish recipients and
  English commands, arguments and explanations for English recipients.
- Keep both command languages accepted while removing alternate aliases from
  help. Match welcome, invitation and usage/privacy hints to the selected language.

## Unreleased — bilingual private bot controls

- Add owner-scoped status/settings, pause/resume, forwarding mode, media-download
  and timed quiet commands with Polish aliases, localized help and a first-reply
  welcome notice. Preserve approval, private-chat and sender checks.
- Apply mode/media/quiet settings without reconnecting Messenger. Skip quiet
  notifications durably, including delayed media and gateway timing races;
  preserve local capture, pairing and receive-only operation.
- Report revision-bound worker health and metadata-only last capture/delivery
  results; label stale/unknown state rather than inferring delivery from replies.
- Preserve settings across restarts and clear old account control/health records
  when approving a new recipient or removing an account.

## Unreleased — per-user Telegram languages

- Default generated Telegram notifications, replies, download buttons, file
  captions and transcript labels to Polish; preserve captured content and media.
- Add persistent `/en` and `/pl` preferences scoped to the verified Telegram
  sender, applying across their topics without restarting their worker.
- Expose only the authenticated worker's read-only preference through the local
  gateway. Keep delivery ownership, single-notice downloads and ambiguous-send
  protection in place. Support `TELEGRAM_LANGUAGE` for standalone deployments.
- Include the translation catalog in containers/installations and add
  `messenger-dashboard-admin restart-active` for the one-time worker upgrade.

## Unreleased — Telegram attachment download buttons

- Add gateway-owned Download file/ZIP buttons to new shared-bot document
  notifications, including voice recordings; preserve the single original notice.
- Handle owner-checked download requests in the existing receiver and reuse the
  Telegram document ID to send one requested copy in the same private topic.
- Keep tokens/URLs/local archives out of button data, preserve the worker method
  allowlist, and limit repeated taps without replaying ambiguous sends.

## Unreleased — server usage and free-tier budgets

- Add an administrator Usage tab with CPU, memory, swap, disk, network rates
  and bounded metadata-only account storage measurements.
- Persist observed monthly outbound traffic and allocated A1 compute estimates
  across manager restarts; handle counter resets and UTC month transitions.
- Add editable, source-dated Oracle budgets, full-month allocation forecasts and
  explicitly manual cloud storage/backup/request totals. Unknown and stale data
  are labelled; local observations are not presented as an Oracle bill.
- Keep sampling isolated from account reconciliation and preserve credential,
  consent, retention, worker and Telegram behavior.

## Unreleased — mobile-first dashboard redesign

- Replace decorative dark gradients with a neutral light workspace, one action
  color, readable type, grouped metrics and restrained status indicators.
- Add mobile hamburger navigation with focus trapping, Escape/backdrop dismissal
  and an inert background; keep a persistent sidebar on desktop.
- Put capture controls first, collapse saved cookie replacement, and simplify
  invitations, bot settings, sign-in and the guest setup pages.
- Keep 44 px controls and the existing mobile typing proxy; verify geometry,
  contrast and overflow at five widths alongside all existing flows.

## Unreleased — mobile remote-browser keyboard

- Collapse the large typing box on phones and coarse-pointer devices. Tapping
  the remote view focuses an invisible input inside the visible viewport, with
  Keyboard, Enter, Done and optional Typing box controls.
- Use prevent-scroll focus and a short, cancellable focus guard rather than
  moving to the below-frame field. Intentional scrolling remains available.
- Keep local autocorrection off and avoid sending Enter/deletion keys while an
  IME composition is active. Clear local input when the session changes or ends.
- Add focus/viewport/gesture unit checks and mobile-sized Chromium/WebKit
  regressions. Native iOS/Android keyboard behavior still needs device testing.

## Unreleased — fix shared Telegram media uploads

- Fix valid media uploads being rejected before delivery when the multipart
  decoder's memory window was smaller than its input chunk size.
- Keep the 32 KiB text-field limit separately from the bounded decoder window;
  preserve file/body/part limits and account destination checks.
- Preserve parser and size rejections as HTTP 4xx instead of masking them as
  ambiguous HTTP 502 failures. Unknown delivery outcomes still are not replayed.
- Test exact binary uploads from 32 KiB through 5 MiB and an encrypted view-once
  fixture through download, local storage and the real loopback gateway.

## Unreleased — recover the Facebook-to-Telegram handoff

- Keep reading saved setup progress while the viewer is active or confirmation
  is pending, so a lost response or slow browser cleanup cannot hide Telegram.
- Pause the viewer and inputs during confirmation, prevent duplicate submits,
  bound HTTP waits and recover completed state without automatically resending.
- Recover from an earlier ambiguous input error only after a fresh read-only
  readiness check; keep explicit consent and server validation in place.
- Recreate screenshot sessions after transient target failures, align readiness
  with the existing confirmation URL checks, and offer reopen after disconnects.
- Add fault-injected HTTPS handoff coverage and capture/concurrency regressions.

## Unreleased — accurate remote clicks after scaling

- Map clicks using the decoded frame's intrinsic dimensions, fixing displaced
  input when responsive CSS or zoom changes the image's displayed size.
- Apply the same scale to touch and wheel scrolling, ignore clicks before a
  frame loads, and avoid rewriting image attributes on every metadata update.
- Test small source-page targets through actual guest taps/clicks at mobile,
  desktop and zoomed sizes, alongside geometry and ordered-input regressions.

## Unreleased — load Arkose MatchKey verification

- Support four specific Arkose API, frame and CDN hosts in both browser egress
  layers, leaving other provider subdomains blocked. Facebook can
  present this provider instead of Google reCAPTCHA during a different session.
- Exercise Google and Arkose synthetic scripts, frames, an API request and image
  dependencies together through the real sandboxed browser and private proxy.
- Retain Facebook-only session confirmation, Google path restrictions,
  private-address rejection and manual verification.

## Unreleased — load Facebook's verification challenge

- Fix the blank Google reCAPTCHA challenge caused by Facebook-only request
  filtering in both Chromium and the host's private proxy.
- Share the host policy and permit the documented reCAPTCHA hosts, with
  `/recaptcha/` path restrictions in Chromium. Confirming a session still
  requires a Facebook URL; general Google pages and other subdomains stay blocked.
- Add policy, private-address and real offline cross-domain frame/script/image
  checks. Verification remains manual; no live CAPTCHA is used in tests.

## Unreleased — smoother remote login browser

- Replace 1.2-second screenshot polling with an authenticated live JPEG stream
  capped at 10 fps, direct compositor capture and lightweight login metadata.
- Batch rapid text/wheel input while preserving click, typing and key order;
  support touch scrolling and avoid replaying ambiguous input requests.
- Pause hidden views, reconnect read-only streams, and provide a decoded snapshot
  fallback without changing session expiry, revocation or browser isolation.
- Add stream authorization/cleanup tests, input queue regressions, browser-flow
  frame-rate/pause checks and an offline real-Chromium capture benchmark.

## Unreleased — self-service setup invitations

- Generate single-use expiring setup links, list active links and recent history,
  and disable access and temporary browsers from the dashboard.
- Consent-based guest onboarding through a real remote Facebook browser,
  encrypted cookie handoff, and an automatic personal Telegram invitation.
- Separate controller and disposable unprivileged sandboxed Chromium containers,
  with a deadline, memory limit, no data/token mounts, and no direct IP network.
- Facebook-only HTTPS egress through an IP-checked Unix-socket proxy; no public
  debugging/VNC ports or recording of inputs/screenshots.
- Atomic claims, independent guest cookies/CSRF, reserved capacity, revocation
  checks at credential commit, and the existing Telegram approval flow.
- Offline browser/container checks and synthetic desktop/mobile guest flows.

## Unreleased — incoming-only capture and combined Telegram downloads

- Exclude the logged-in account's regular/encrypted messages before storage or
  media downloads, and remove quoted copies of its text from incoming records.
- Restrict related actions to known incoming messages; briefly hold unattributed
  deletions in memory for delayed originals. Read-only views hide legacy owner records.
- Send each deletion as one notice: text, an original downloadable attachment with
  a caption, or one ZIP containing multiple files and full message details.
- Keep voice files downloadable, wait for pending media, preserve long text in a
  transcript, and retain duplicate suppression and safe missing-topic repair.

## Unreleased — administrator workspace

- Optional HTTPS admin dashboard for up to six separate Messenger accounts.
- Encrypted cookie/bot configuration, expiring sign-in sessions, and CSRF protection.
- One shared Telegram gateway with destination checks, paced sends, single-use
  invitations, administrator-approved pairing, and per-user disconnect.
- Separate root container manager; web/bot services have no Docker access.
- Mounted runtime worker credentials, independent archives, account lifecycle
  controls, stopped-account retention, and original-service migration/rollback.
- Synthetic isolation/migration tests and a real-browser account setup/mobile check.

## Unreleased — initial open-source edition

- Receive-only regular/encrypted capture with a pinned native bridge.
- Future-events-only admission, per-run counters, resolved names, and content-only logs.
- Observed deletion tracking with original/latest captured text and media.
- Bounded authenticated media downloads and supported view-once wrapper parsing.
- Optional formatted Telegram notices and persistent per-conversation topics.
- Fresh read-only Telegram setup checks and local topic-ID repair.
- Rolling 72-hour retention, recoverable file/journal cleanup, and late-download
  protection; remote Telegram copies remain untouched.
- Ubuntu systemd installation, controlled restarts, and an independent cleanup timer.
- Synthetic tests, Docker/native CI configuration, generic setup guides,
  example configuration, and upstream license/provenance records.
