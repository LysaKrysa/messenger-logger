# Transport provenance

## Optional browser onboarding

`browser/seccomp.json` is the Chromium sandbox policy distributed by Microsoft
Playwright v1.64.1, under Apache-2.0. Source:
https://github.com/microsoft/playwright/blob/v1.64.1/utils/docker/seccomp_profile.json
The license is included in `LICENSES/Apache-2.0.txt`.
The policy additionally allows Chromium's sandbox `chroot` syscall inside its
user namespace when the container's Linux capabilities are all dropped. No
capability is added to the container.

The separate browser image installs Playwright 1.64.1 and Debian's Chromium
package with their dependency license records retained in the image. These are
not added to the standalone Messenger logger image. The original browser agent,
controller, restricted proxy, guest UI and tests use this project's license.

## License scope

Original files in this standalone project are licensed under AGPL-3.0-or-later.
That grant does not replace upstream licenses or claim rights over the former
Railway/fbchat-v1 implementation, which is not included here.

| Component | Upstream license record | Included reference |
| --- | --- | --- |
| Original adapter, docs, tests, native additions/build hooks | AGPL-3.0-or-later | `LICENSE`, `NOTICE` |
| fbchat-v2 upstream root | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| mautrix-meta | AGPL-3.0-or-later, with its upstream exception record | `LICENSE`, `LICENSES/mautrix-meta.exceptions.txt` |
| Transitive Go dependencies | Their respective upstream licenses | Exact upstream `go.mod`/`go.sum` at the pinned revisions |
| Optional dashboard: Flask and Werkzeug | BSD-3-Clause | [Pallets license](https://github.com/pallets/flask/blob/main/LICENSE.txt); installed package metadata includes dependency notices |
| Optional dashboard: cryptography | Apache-2.0 OR BSD-3-Clause | [Upstream license](https://github.com/pyca/cryptography/blob/main/LICENSE) |
| Optional dashboard: Gunicorn | MIT | [Upstream license](https://github.com/benoitc/gunicorn/blob/master/LICENSE) |

The upstream bridge README also credits code derived from
[meta-messenger.js](https://github.com/yumi-team/meta-messenger.js). Consult the
license notices in the exact fetched source for the native build as a whole.
Upstream files retain their authorship and license notices.

The repository distributes source additions and a reproducible build recipe,
not prebuilt binaries or images. Preserve applicable notices and corresponding
source when redistributing a compiled transport or container. The runtime image
also includes this project's license and the recorded license references under
`/usr/share/licenses/messenger-logger/`.

## Pinned source and binary verification

The Python adapter, event journal, downloader, and tests in this source tree were
written for the standalone implementation. The native transport is built from pinned
source during the Docker build; no precompiled binary is included in this archive.

* Upstream: https://github.com/m008v/fbchat-v2
* Release: `v2.3.4`
* Source commit: `aa7abd0386ef1d4cabf8ce592d0f2cf308d8f4f6`
* Bridge source: https://github.com/m008v/fbchat-v2/tree/aa7abd0386ef1d4cabf8ce592d0f2cf308d8f4f6/bridge-e2ee
* Pinned mautrix-meta submodule: `cea2e38c9107be591bcd1b7a6562ccc72400ce23`
* Customized bridge version: `2.3.4-logger.2`
* Local additions and tests: `native/logger_media.go`, `native/instagram.go`, and their corresponding test files.
* Exact source edits and build procedure: `build_native.py`
* Compiler: Go `1.26.10`; CGO disabled, `-trimpath`, `-buildvcs=false`,
  `-ldflags=-s -w -buildid=`. No module versions are upgraded or tidied.

The upstream Python distribution is Apache-2.0. Its native bridge uses
mautrix-meta and other upstream components with their own licenses, including
AGPL-3.0 components. Consult the exact upstream source and its bridge README
before redistributing a built container or binary. This archive does not
relicense those dependencies.

Pinned rebuilt binary SHA-256 (recomputed from tested local builds):

| Linux architecture | Digest |
| --- | --- |
| amd64 | `81fd4b1bc0ef4710a6e1ff9de8b426623386ada987db3142547c675628f78f35` |
| arm64 | `0261e78e5e5e6dea9ce82f4bdb31caa39217ae2112d16ddefa0755fb859a6095` |

The current standalone interface is JSON-line RPC. The adapter exposes only
`hello`, `newClient`, `connect`, `connectE2EE`, `isConnected`, `disconnect`,
`downloadMedia`, `downloadE2EEMedia`, and the bounded read-only `getUserInfo` lookup.
The media implementation delegates
download/decryption to those upstream methods; it contains no copied crypto.
The source archive includes no cookie files, API tokens, native device state,
message data, or copied upstream implementation.

Local changes add native extraction of Consumer/Raven view-once media, safe
contact/thread metadata forwarding, and a bounded contact-name lookup. The
existing native media downloader performs decryption and integrity checks.
The Python adapter continues to exclude all message-changing Messenger methods.
