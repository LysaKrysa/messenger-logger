# Changelog

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
