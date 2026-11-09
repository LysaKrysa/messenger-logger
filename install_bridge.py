"""Install and verify the locally rebuilt, pinned Messenger bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

VERSION = "2.3.4-logger.2"
SOURCE_REVISION = "aa7abd0386ef1d4cabf8ce592d0f2cf308d8f4f6"
META_REVISION = "cea2e38c9107be591bcd1b7a6562ccc72400ce23"
# Go 1.26.10, CGO disabled, trimpath, no VCS/build ID.
DIGESTS = {
    "amd64": "81fd4b1bc0ef4710a6e1ff9de8b426623386ada987db3142547c675628f78f35",
    "arm64": "0261e78e5e5e6dea9ce82f4bdb31caa39217ae2112d16ddefa0755fb859a6095",
}
UPSTREAM_DIGESTS = {
    "amd64": "6de0ce8db4986da2fd471385d63b61a5334084edb78bc6de2f53ecea6500189d",
    "arm64": "4d61087632c2c157294502265cb1e8807c0c5ac9c3d8addca2c109dd1eec1544",
}
CAPABILITIES = {"newClient", "connect", "connectE2EE", "isConnected", "events", "structuredConnectionStatus", "viewOnceMedia", "contactLookup", "instagramDMs"}


def install(destination: Path, source: Path):
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if platform.system() != "Linux" or arch not in DIGESTS:
        raise RuntimeError("This image supports Linux amd64 and arm64.")
    expected = DIGESTS[arch]
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Rebuilt Messenger bridge SHA-256 check failed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output, source.open("rb") as handle:
            temporary = Path(output.name)
            while block := handle.read(65536):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, destination)
        destination.with_suffix(destination.suffix + ".sha256").write_text(expected + "\n", encoding="ascii")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Installed Messenger bridge " + VERSION + "; SHA-256 verified.")


def smoke_test(binary: Path):
    """Local RPC only: no account, authentication, or messaging."""
    requests = [{"id": index, "method": method, "params": {}} for index, method in enumerate(("hello", "isConnected", "disconnect"), 1)]
    result = subprocess.run([str(binary.resolve())], input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True, timeout=20)
    if result.returncode != 0:
        raise RuntimeError("Bridge process smoke test failed.")
    replies = {item["id"]: item for line in result.stdout.splitlines() if (item := json.loads(line)).get("id")}
    hello = replies.get(1, {}).get("data", {})
    if (hello.get("bridgeVersion") != VERSION or hello.get("protocolVersion") != 1
            or not CAPABILITIES.issubset(set(hello.get("capabilities", [])))
            or replies.get(2, {}).get("ok") is not True or replies[2].get("data", {}).get("connected") is not False
            or replies.get(3, {}).get("ok") is not True):
        raise RuntimeError("Bridge version, capability, or disconnected-state check failed. Rebuild the image for this update.")
    print("Bridge RPC smoke test passed (no Facebook login attempted).")


if __name__ == "__main__":
    try:
        destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/local/bin/messenger-bridge")
        source = Path(sys.argv[2] if len(sys.argv) > 2 else "/build/messenger-bridge")
        install(destination, source)
        smoke_test(destination)
    except Exception as exc:
        print("Bridge installation failed (" + type(exc).__name__ + "); no credentials were involved.", file=sys.stderr)
        raise SystemExit(1)
