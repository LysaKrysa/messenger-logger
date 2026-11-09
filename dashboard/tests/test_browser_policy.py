"""Verification dependencies load without opening unrelated egress or capture."""
import socket
import unittest
from unittest.mock import patch

from browser.policy import RECAPTCHA_HOSTS, allowed_host, allowed_url, facebook_url, session_url
from dashboard.browser_proxy import public_addresses

ARKOSE_HOSTS = {"meta-api.arkoselabs.com", "client-api.arkoselabs.com", "iframe.arkoselabs.com", "cdn.arkoselabs.com"}


class BrowserPolicyTests(unittest.TestCase):
    def test_readiness_and_confirmation_use_the_existing_checkpoint_url_boundary(self):
        for path in ("/login/", "/checkpoint/", "/recover/initiate", "/CHECKPOINT/"):
            self.assertFalse(session_url("https://www.facebook.com" + path))
        for url in ("https://www.facebook.com/", "https://m.facebook.com/", "https://www.facebook.com/messages/"):
            self.assertTrue(session_url(url))
        for url in ("about:blank", "https://www.google.com/recaptcha/api.js", "https://meta-api.arkoselabs.com/", "http://www.facebook.com/"):
            self.assertFalse(session_url(url))

    def test_specific_arkose_api_frame_and_cdn_are_allowed_in_both_layers(self):
        for host, path in (
            ("meta-api.arkoselabs.com", "/v2/SYNTHETIC-KEY/api.js"),
            ("client-api.arkoselabs.com", "/fc/api/"),
            ("iframe.arkoselabs.com", "/fixture/frame.html"),
            ("cdn.arkoselabs.com", "/fc/assets/fixture.gif"),
        ):
            with self.subTest(host=host, path=path):
                self.assertTrue(allowed_host(host))
                self.assertTrue(allowed_url("https://" + host + path))

    def test_arkose_host_scope_excludes_other_tenants_and_lookalike_domains(self):
        for host in ("other-tenant-api.arkoselabs.com", "unlisted.arkoselabs.com", "evil.cdn.arkoselabs.com", "arkoselabs.com", "evilarkoselabs.com", "meta-api.arkoselabs.com.evil.example", "arkoselabs.com.evil.example", "arkoselabs.localhost", "arkoselabs.com@127.0.0.1"):
            with self.subTest(host=host):
                self.assertFalse(allowed_host(host))
                self.assertFalse(allowed_url("https://" + host + "/v2/fixture/api.js"))
        self.assertTrue(allowed_host("CDN.ARKOSELABS.COM."))
        self.assertTrue(allowed_url("https://CDN.ARKOSELABS.COM./fc/assets/fixture.gif"))

    def test_arkose_urls_cannot_confirm_a_facebook_session(self):
        for host in ARKOSE_HOSTS:
            for path in ("/", "/v2/fixture/api.js", "/https://www.facebook.com/"):
                self.assertFalse(facebook_url("https://" + host + path))

    def test_documented_verification_scripts_frames_and_payloads_are_allowed(self):
        for url in (
            "https://www.google.com/recaptcha/enterprise.js?render=synthetic",
            "https://www.google.com/recaptcha/api2/anchor?synthetic=1",
            "https://www.google.com/recaptcha/enterprise/reload",
            "https://www.google.com/recaptcha/api2/payload",
            "https://recaptcha.google.com/recaptcha/enterprise/bframe",
            "https://www.gstatic.com/recaptcha/releases/synthetic/recaptcha__en.js",
            "https://www.recaptcha.net/recaptcha/enterprise.js",
            "https://www.recaptcha.net/recaptcha/api2/anchor",
        ):
            with self.subTest(url=url):
                self.assertTrue(allowed_url(url))

    def test_proxy_and_browser_share_exact_third_party_hosts(self):
        for host in RECAPTCHA_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(allowed_host(host))
                self.assertTrue(allowed_url("https://" + host + "/recaptcha/api.js"))
        for host in ("google.com", "accounts.google.com", "evil.www.google.com", "www.google.com.evil.example", "unlisted.gstatic.com", "recaptcha.net", "evilrecaptcha.net", "127.0.0.1", "169.254.169.254"):
            with self.subTest(host=host):
                self.assertFalse(allowed_host(host))
                self.assertFalse(allowed_url("https://" + host + "/recaptcha/api.js"))

    def test_third_party_requests_are_restricted_to_recaptcha_paths(self):
        for path in ("/", "/search?q=synthetic", "/accounts", "/unrelated.js", "/recaptcha-evil/api.js", "/recaptcha/../search", "/recaptcha/%2e%2e/search", "/recaptcha/%2e%2e%5csearch"):
            for host in RECAPTCHA_HOSTS:
                with self.subTest(host=host, path=path):
                    self.assertFalse(allowed_url("https://" + host + path))

    def test_verification_resources_cannot_confirm_a_facebook_session(self):
        for host in RECAPTCHA_HOSTS:
            self.assertFalse(facebook_url("https://" + host + "/recaptcha/api.js"))
        self.assertTrue(facebook_url("https://www.facebook.com/"))
        self.assertTrue(facebook_url("https://m.facebook.com/"))

    def test_https_port_and_credential_restrictions_apply_to_all_hosts(self):
        for host in ("www.facebook.com", "www.google.com", "www.gstatic.com", *ARKOSE_HOSTS):
            for url in ("http://" + host + "/recaptcha/api.js", "https://" + host + ":8443/recaptcha/api.js", "https://user:secret@" + host + "/recaptcha/api.js", "https://" + host + ":invalid/recaptcha/api.js"):
                with self.subTest(url=url):
                    self.assertFalse(allowed_url(url))
                    self.assertFalse(facebook_url(url))
        for url in (None, {}, "https://[", "file:///etc/passwd"):
            self.assertFalse(allowed_url(url))

    def test_captcha_dns_cannot_resolve_to_private_or_metadata_addresses(self):
        for host in RECAPTCHA_HOSTS | ARKOSE_HOSTS:
            for address in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fd00::1"):
                with patch("dashboard.browser_proxy.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]):
                    with self.assertRaises(ValueError):
                        public_addresses(host)

    def test_instagram_egress_and_session_readiness_are_provider_scoped(self):
        self.assertTrue(allowed_host("www.instagram.com"))
        self.assertTrue(allowed_host("static.cdninstagram.com"))
        for url in ("https://www.instagram.com/accounts/login/", "https://static.cdninstagram.com/script.js",
                    "https://static.xx.fbcdn.net/script.js", "https://www.google.com/recaptcha/api.js"):
            self.assertTrue(allowed_url(url, "instagram"))
        for url in ("https://www.facebook.com/", "https://www.instagram.com.evil.example/",
                    "https://www.instagram.com:8443/", "https://@www.instagram.com/",
                    "https://www.google.com/search", "https://169.254.169.254/"):
            self.assertFalse(allowed_url(url, "instagram"))
        self.assertFalse(allowed_url("https://www.instagram.com/"))
        for url in ("https://www.instagram.com/", "https://www.instagram.com/direct/inbox/",
                    "https://www.instagram.com/direct/t/123/"):
            self.assertTrue(session_url(url, "instagram"))
        for url in ("https://www.instagram.com/accounts/login/", "https://www.instagram.com/challenge/",
                    "https://www.instagram.com/accounts/two_factor_authentication/",
                    "https://accountscenter.instagram.com/", "https://static.cdninstagram.com/",
                    "https://www.facebook.com/", "https://www.instagram.com/direct/../challenge/"):
            self.assertFalse(session_url(url, "instagram"))


if __name__ == "__main__":
    unittest.main()
