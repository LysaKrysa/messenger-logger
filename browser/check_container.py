"""Real Chromium isolation and sign-in boundary checks using only local fixtures."""
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.browser_runtime import DockerBrowser
from dashboard.browser_proxy import Connect, Proxy


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        host = self.headers.get("Host")
        self.server.seen.add((host, path))
        content_type = "text/html"
        cookies = ()
        if host == "www.instagram.com" and path == "/accounts/login/":
            body = b'<title>Synthetic Instagram sign-in</title><a href="/direct/inbox/">Synthetic Inbox</a>'
            cookies = ("ds_user_id=54321", "sessionid=SYNTHETIC_IG_SESSION", "csrftoken=SYNTHETIC_IG_CSRF")
        elif host == "www.instagram.com" and path == "/direct/inbox/":
            body = b"""<title>Synthetic Instagram inbox</title>
<script src="https://static.cdninstagram.com/synthetic-instagram.js"></script>
<script src="https://www.facebook.com/unrelated-instagram.js"></script>
<script src="https://instagram.com.evil.example/unrelated-instagram.js"></script>"""
        elif path == "/synthetic-instagram.js":
            content_type = "application/javascript"
            body = b"globalThis.syntheticInstagramAsset = true;"
        elif path == "/":
            body = b"""<title>Offline HTTPS proxy fixture</title><h1>Synthetic local egress test</h1>
<script>
globalThis.syntheticChallenge = {script:false, frame:false, alternative:false};
globalThis.syntheticArkose = {script:false, client:false, frame:false};
globalThis.syntheticUnrelated = false;
addEventListener('message', event => {
  if (event.origin === 'https://recaptcha.google.com' && event.data === 'SYNTHETIC_FRAME_READY') syntheticChallenge.frame = true;
  if (event.origin === 'https://iframe.arkoselabs.com' && event.data === 'SYNTHETIC_ARKOSE_READY') syntheticArkose.frame = true;
});
</script>
<script src="https://www.google.com/recaptcha/enterprise.js"></script>
<script src="https://www.recaptcha.net/recaptcha/api.js"></script>
<script src="https://meta-api.arkoselabs.com/v2/SYNTHETIC-KEY/api.js"></script>
<script src="https://client-api.arkoselabs.com/fc/api.js"></script>
<script src="https://www.google.com/search"></script>
<script src="https://www.gstatic.com/unrelated.js"></script>
<script src="https://evilarkoselabs.com/fixture.js"></script>
<script src="https://meta-api.arkoselabs.com.evil.example/fixture.js"></script>
<script src="https://other-tenant-api.arkoselabs.com/fixture.js"></script>
<script src="https://unlisted.gstatic.com/recaptcha/unrelated.js"></script>"""
        elif path == "/recaptcha/enterprise.js":
            content_type = "application/javascript"
            body = b"syntheticChallenge.script = true; const frame = document.createElement('iframe'); frame.src = 'https://recaptcha.google.com/recaptcha/enterprise/anchor'; document.body.appendChild(frame);"
        elif path == "/recaptcha/api.js":
            content_type = "application/javascript"
            body = b"syntheticChallenge.alternative = true;"
        elif path == "/recaptcha/enterprise/anchor":
            body = b'<h1>Synthetic verification frame</h1><script src="https://www.gstatic.com/recaptcha/releases/offline/fixture.js"></script>'
        elif path == "/recaptcha/releases/offline/fixture.js":
            content_type = "application/javascript"
            body = b"const image = new Image(); image.onload = () => parent.postMessage('SYNTHETIC_FRAME_READY', 'https://fixture.facebook.com'); image.src = 'https://www.google.com/recaptcha/api2/payload'; document.body.appendChild(image);"
        elif path == "/recaptcha/api2/payload":
            content_type = "image/gif"
            body = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        elif path == "/v2/SYNTHETIC-KEY/api.js":
            content_type = "application/javascript"
            body = b"syntheticArkose.script = true; const arkoseFrame = document.createElement('iframe'); arkoseFrame.src = 'https://iframe.arkoselabs.com/fixture/frame.html'; document.body.appendChild(arkoseFrame);"
        elif path == "/fc/api.js":
            content_type = "application/javascript"
            body = b"syntheticArkose.client = true;"
        elif path == "/fixture/frame.html":
            body = b'<h1>Synthetic Arkose resource test</h1><script src="https://cdn.arkoselabs.com/fc/assets/fixture.js"></script>'
        elif path == "/fc/assets/fixture.js":
            content_type = "application/javascript"
            body = b"""(async () => {
const response = await fetch('https://meta-api.arkoselabs.com/fixture/session', {method:'POST', body:'SYNTHETIC_RESOURCE_TEST'});
if ((await response.json()).status !== 'synthetic-ready') throw new Error('Synthetic API did not respond');
const image = new Image(); image.onload = () => parent.postMessage('SYNTHETIC_ARKOSE_READY', 'https://fixture.facebook.com');
image.src = 'https://cdn.arkoselabs.com/fc/assets/fixture.gif'; document.body.appendChild(image);
})();"""
        elif path == "/fc/assets/fixture.gif":
            content_type = "image/gif"
            body = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        else:
            content_type = "application/javascript"
            body = b"globalThis.syntheticUnrelated = true;"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie + "; Path=/; Secure; HttpOnly; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlsplit(self.path).path
        self.server.seen.add((self.headers.get("Host"), path))
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        assert path == "/fixture/session" and body == b"SYNTHETIC_RESOURCE_TEST"
        response = b'{"status":"synthetic-ready"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "https://iframe.arkoselabs.com")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def main():
    with tempfile.TemporaryDirectory(prefix="messenger-browser-check-") as directory:
        path = Path(directory) / "egress.sock"
        certificate, key = Path(directory) / "fixture.crt", Path(directory) / "fixture.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=fixture.facebook.com", "-keyout", str(key), "-out", str(certificate)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        fixture.seen = set()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificate, key)
        fixture.socket = tls.wrap_socket(fixture.socket, server_side=True)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        with Proxy(str(path), Connect) as proxy:
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o600)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            browser = DockerBrowser(uuid.uuid4().hex, time.time() + 120, proxy_path=path, policy_path=Path(__file__).resolve().with_name("seccomp.json"), offline_check=True)
            try:
                result = browser.call("smoke")
                assert result["sandboxed"] and result["title"] == "Offline browser fixture"
                browser.call("click", {"x": 50, "y": 15})
                browser.call("text", {"text": "OFFLINE_SYNTHETIC_INPUT"})
                assert browser.call("smoke-check")["input"] == "OFFLINE_SYNTHETIC_INPUT"
                browser.call("input", {"events": [
                    {"action": "key", "key": "Control+A"},
                    {"action": "text", "text": "BATCHED_SYNTHETIC_INPUT"},
                ]})
                assert browser.call("smoke-check")["input"] == "BATCHED_SYNTHETIC_INPUT"
                assert "jpeg" not in browser.call("frame", {"metadata_only": True})
                frame = browser.call("frame")
                assert base64.b64decode(frame["jpeg"]).startswith(b"\xff\xd8") and not frame["ready"]
                timings = []
                for _ in range(12):
                    began = time.monotonic()
                    browser.call("frame")
                    timings.append(time.monotonic() - began)
                assert sum(timings) < 3, "Direct viewport capture did not sustain five frames per second."
                print("Offline direct-capture median: {:.0f} ms; slowest: {:.0f} ms.".format(statistics.median(timings) * 1000, max(timings) * 1000))
                inspected = subprocess.run(["docker", "inspect", browser.name], check=True, capture_output=True)
                info = json.loads(inspected.stdout)[0]
                host = info["HostConfig"]
                assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
                assert host["LogConfig"]["Type"] == "none" and not host["PortBindings"]
                assert info["Config"]["User"] == "10001:10001" and host["Memory"] == 1024 ** 3
                assert host["CapDrop"] == ["ALL"] and len(info["Mounts"]) == 1 and not info["Mounts"][0]["RW"]
                # Explicit test-only DNS replacement: the real browser crosses
                # its local tunnel and mounted Unix socket to this host fixture.
                # The production resolver never permits loopback addresses.
                with patch("dashboard.browser_proxy.public_addresses", return_value=[(socket.AF_INET, ("127.0.0.1", fixture.server_port))]):
                    network = browser.call("smoke-network")
                    assert network["title"] == "Offline HTTPS proxy fixture"
                    assert network["challenge_loaded"] and network["arkose_loaded"] and network["unrelated_blocked"]
                    for request in (
                        ("www.google.com", "/recaptcha/enterprise.js"),
                        ("recaptcha.google.com", "/recaptcha/enterprise/anchor"),
                        ("www.gstatic.com", "/recaptcha/releases/offline/fixture.js"),
                        ("www.google.com", "/recaptcha/api2/payload"),
                        ("www.recaptcha.net", "/recaptcha/api.js"),
                        ("meta-api.arkoselabs.com", "/v2/SYNTHETIC-KEY/api.js"),
                        ("client-api.arkoselabs.com", "/fc/api.js"),
                        ("iframe.arkoselabs.com", "/fixture/frame.html"),
                        ("cdn.arkoselabs.com", "/fc/assets/fixture.js"),
                        ("meta-api.arkoselabs.com", "/fixture/session"),
                        ("cdn.arkoselabs.com", "/fc/assets/fixture.gif"),
                    ):
                        assert request in fixture.seen, "A synthetic verification dependency did not traverse the private proxy."
                    for request in (("www.google.com", "/search"), ("www.gstatic.com", "/unrelated.js"), ("unlisted.gstatic.com", "/recaptcha/unrelated.js")):
                        assert request not in fixture.seen, "An unrelated Google request was allowed."
                    for host in ("evilarkoselabs.com", "meta-api.arkoselabs.com.evil.example", "other-tenant-api.arkoselabs.com"):
                        assert (host, "/fixture.js") not in fixture.seen, "An Arkose lookalike domain was allowed."
                    browser.close()
                    browser = DockerBrowser(uuid.uuid4().hex, time.time() + 120, proxy_path=path,
                                            policy_path=Path(__file__).resolve().with_name("seccomp.json"),
                                            offline_check=True, platform="instagram")
                    network = browser.call("smoke-network")
                    assert network["title"] == "Synthetic Instagram inbox" and network["ready"]
                    assert browser.call("frame", {"metadata_only": True})["ready"]
                    assert ("www.instagram.com", "/accounts/login/") in fixture.seen
                    assert ("static.cdninstagram.com", "/synthetic-instagram.js") in fixture.seen
                    assert ("www.facebook.com", "/unrelated-instagram.js") not in fixture.seen
                    assert ("instagram.com.evil.example", "/unrelated-instagram.js") not in fixture.seen
            finally:
                browser.close()
                proxy.shutdown()
                fixture.shutdown()
                fixture.server_close()
    print("Sandboxed browser, HTTPS proxy, synthetic verification resources and Instagram sign-in boundary passed.")


if __name__ == "__main__":
    main()
