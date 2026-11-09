"""Shared HTTPS egress policy for the browser and its CONNECT proxy."""
from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit

FACEBOOK_DOMAINS = ("facebook.com", "facebook.net", "fbcdn.net", "fbsbx.com", "messenger.com")
INSTAGRAM_DOMAINS = ("instagram.com", "cdninstagram.com")
META_ASSETS = ("facebook.net", "fbcdn.net", "fbsbx.com")
LOGIN_URLS = {"facebook": "https://www.facebook.com/", "instagram": "https://www.instagram.com/accounts/login/"}
# Google's documented reCAPTCHA scripts, frames, API and alternate endpoint:
# https://docs.cloud.google.com/recaptcha/docs/faq
RECAPTCHA_HOSTS = frozenset({"www.google.com", "recaptcha.google.com", "www.gstatic.com", "www.recaptcha.net"})
# Permit only the specific Arkose client API, challenge frame and CDN hosts
# used by this integration; do not open the entire provider domain family.
# https://developer.arkoselabs.com/docs/domain-policy
ARKOSE_HOSTS = frozenset({"meta-api.arkoselabs.com", "client-api.arkoselabs.com", "iframe.arkoselabs.com", "cdn.arkoselabs.com"})


def domain_host(host, domains):
    if not isinstance(host, str):
        return False
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def facebook_host(host):
    return domain_host(host, FACEBOOK_DOMAINS)


def allowed_host(host):
    """CONNECT sees a hostname, never the encrypted request path."""
    return isinstance(host, str) and (domain_host(host, FACEBOOK_DOMAINS + INSTAGRAM_DOMAINS)
                                      or arkose_host(host) or host.lower().rstrip(".") in RECAPTCHA_HOSTS)


def arkose_host(host):
    return isinstance(host, str) and host.lower().rstrip(".") in ARKOSE_HOSTS


def https_url(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.port in {None, 443} and parsed.hostname and parsed.username is None and parsed.password is None:
            return parsed
    except (TypeError, ValueError):
        pass
    return None


def facebook_url(url):
    """Only Facebook URLs can be used to confirm the captured session."""
    parsed = https_url(url)
    return parsed is not None and facebook_host(parsed.hostname)


def session_url(url, platform="facebook"):
    """Use the same existing URL eligibility for readiness and confirmation."""
    parsed = https_url(url)
    if parsed is None:
        return False
    if platform == "instagram":
        # The pinned Instagram connector recognizes Home and Direct after
        # sign-in. A cookie on a login, challenge or settings URL is insufficient.
        path = parsed.path.rstrip("/")
        return parsed.hostname in {"instagram.com", "www.instagram.com"} and (
            path in {"", "/direct", "/direct/inbox"} or re.fullmatch(r"/direct/t/[0-9]+", path) is not None
        )
    return platform == "facebook" and facebook_host(parsed.hostname) and not any(
        part in parsed.path.lower() for part in ("login", "checkpoint", "recover")
    )


def allowed_url(url, platform="facebook"):
    parsed = https_url(url)
    if parsed is None or platform not in LOGIN_URLS:
        return False
    domains = FACEBOOK_DOMAINS if platform == "facebook" else INSTAGRAM_DOMAINS + META_ASSETS
    if domain_host(parsed.hostname, domains) or arkose_host(parsed.hostname):
        return True
    # The browser can restrict paths before TLS; the host proxy also checks
    # these exact hosts and pins them to verified public DNS addresses.
    path = posixpath.normpath(unquote(parsed.path).replace("\\", "/"))
    return parsed.hostname.lower().rstrip(".") in RECAPTCHA_HOSTS and path.startswith("/recaptcha/")
