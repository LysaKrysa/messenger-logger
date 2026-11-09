# Native transport and reproducible builds

[← README](../README.md) · [Exact provenance](../THIRD_PARTY.md)

## Source inputs

| Input | Pin |
| :--- | :--- |
| fbchat-v2 | `aa7abd0386ef1d4cabf8ce592d0f2cf308d8f4f6` |
| mautrix-meta submodule | `cea2e38c9107be591bcd1b7a6562ccc72400ce23` |
| Customized protocol version | `2.3.4-logger.2` |
| Go toolchain | `1.26.10` |
| Platform | Linux amd64 or arm64, CGO disabled |

`build_native.py` fetches the exact revisions, verifies checkout SHAs, applies
explicit single-match hooks, and copies `native/logger_media.go` plus tests.
The pinned upstream `go.mod` and `go.sum` control transitive modules. The build
does not run `go get -u` or `go mod tidy`.

Hooks add view-once decoding, safe contact/thread forwarding, and bounded
read-only lookup. Upstream code handles transport, download, decryption, and
integrity validation. No replacement cryptographic implementation is provided.

## Local native build

With Git, Python 3, and Go 1.26.10 on Linux:

```bash
python3 build_native.py ./build/messenger-bridge
BRIDGE_TEST_BIN="$PWD/build/messenger-bridge" \
  python3 -m unittest discover -s tests -v
```

The builder runs the native Go suite before compiling, writes a SHA-256 file
beside the binary, and uses `-trimpath`, disabled VCS stamping, stripped debug
information, and an empty build ID.

To keep the prepared source for inspection:

```bash
python3 build_native.py ./build/messenger-bridge --work ./native-work
```

To prepare only, without compiling:

```bash
python3 build_native.py ./build/messenger-bridge --work ./native-work --prepare-only
```

`--work` must name a new directory. `--source` can use an exact local upstream
checkout; the parent and submodule revisions are verified before copying it.
`--arch` selects amd64/arm64. Use `--skip-tests` only for cross-compilation,
after running native tests for a runnable host architecture.

## Docker build

```bash
sudo docker build -t messenger-logger:modern .
sudo docker run --rm --network none messenger-logger:modern --self-test
```

The multi-stage Dockerfile uses a Go builder, then copies only the static binary
and Python adapter into the runtime image. No Go compiler is needed at runtime.
The install step verifies the binary checksum and local RPC contract.

The credential-free smoke test calls only `hello`, `isConnected`, and
`disconnect`. It does not log in, register a device, or send messages.

## Updating native code

A source/toolchain change can change the binary hash. Reproduce builds, review
source/module changes, run native tests, and update both architecture pins only
after verification. Do not accept an arbitrary downloaded binary or remove the
hash check to work around a mismatch.

The existing amd64 build was independently reproduced, and both architecture
digests are recorded in THIRD_PARTY.md. The arm64 binary was cross-compiled;
execution must be checked on an arm64 host.

## Licenses and binary distribution

Original project files are AGPL-3.0-or-later. Upstream code and transitive
dependencies retain their licenses. License copies and exceptions are recorded
in `LICENSES/` and THIRD_PARTY.md.

This repository includes source/build additions, not a prebuilt native binary
or container release. If you distribute a built image/binary, preserve required
notices and provide the corresponding source/build inputs required by applicable
upstream licenses. A reproducible recipe is useful provenance, not a change to
those obligations.
