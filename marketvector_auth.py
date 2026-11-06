"""MarketVector small-client login gate (fail closed).

User-password hashes are read ONLY from Streamlit Secrets, never from GitHub.
For high-value or large-scale deployments, prefer a managed OIDC provider.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import threading
import time
from datetime import datetime, timezone
from typing import Mapping

HASH_SCHEME = "pbkdf2_sha256"
MIN_ITERATIONS = 600_000
DEFAULT_SESSION_HOURS = 8
MAX_FAILED_ATTEMPTS = 6
FAILURE_WINDOW_SEC = 15 * 60

# In-process rate limiting supplements (not replaces) external protections.
# Streamlit restarts/replicas do not share this state.
_FAILURES: dict[str, list[float]] = {}
_FAILURE_LOCK = threading.Lock()
_USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")
_STATE_KEY = "_mv_secure_login_identity_v1"


def make_password_hash(password: str, *, iterations: int = MIN_ITERATIONS, salt: bytes | None = None) -> str:
    """Used by the OFFLINE admin script, not in the UI."""
    import secrets
    if not password or len(password) < 14:
        raise ValueError("Use a unique password of at least 14 characters")
    if not (MIN_ITERATIONS <= iterations <= 5_000_000):
        raise ValueError("Invalid PBKDF2 iteration count")
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join((HASH_SCHEME, str(iterations), base64.urlsafe_b64encode(salt).decode("ascii"), base64.urlsafe_b64encode(digest).decode("ascii")))


def check_password(password: str, stored: str) -> bool:
    """Constant-time digest comparison. Unknown/bad records still cost a PBKDF2 call."""
    try:
        scheme, iterations_raw, salt_raw, digest_raw = str(stored).split("$", 3)
        iterations = int(iterations_raw)
        if scheme != HASH_SCHEME or not MIN_ITERATIONS <= iterations <= 5_000_000:
            raise ValueError("Bad password hash metadata")
        salt = base64.urlsafe_b64decode(salt_raw)
        expected = base64.urlsafe_b64decode(digest_raw)
        if not (16 <= len(salt) <= 64 and len(expected) == 32):
            raise ValueError("Bad password hash bytes")
    except (ValueError, TypeError, AttributeError):
        iterations, salt, expected = MIN_ITERATIONS, b"\0" * 16, b"\0" * 32
    except Exception:
        iterations, salt, expected = MIN_ITERATIONS, b"\0" * 16, b"\0" * 32
    try:
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def _load_auth_settings(st) -> tuple[dict[str, dict], float] | None:
    try:
        cfg = st.secrets["marketvector_access"]
        raw_users = cfg.get("users", {})
        if not isinstance(raw_users, Mapping):
            return None
        users = {}
        for name, record in raw_users.items():
            uname = str(name).strip().lower()
            if not _USERNAME_RE.fullmatch(uname) or not isinstance(record, Mapping):
                continue
            users[uname] = {
                "password_hash": str(record.get("password_hash", "")),
                "enabled": bool(record.get("enabled", False)),
            }
        hours = float(cfg.get("session_hours", DEFAULT_SESSION_HOURS))
        if not 0.25 <= hours <= 24:
            hours = DEFAULT_SESSION_HOURS
        if not users:
            return None
        return users, hours * 3600
    except Exception:
        # A missing, malformed, or unreadable secrets configuration fails closed.
        return None


def _session_valid(identity, users, ttl) -> bool:
    if not isinstance(identity, dict):
        return False
    name = identity.get("username")
    record = users.get(name)
    if not record or not record.get("enabled"):
        return False
    if not hmac.compare_digest(str(identity.get("password_hash_fingerprint", "")), hashlib.sha256(record["password_hash"].encode()).hexdigest()):
        return False
    try:
        age = time.time() - float(identity["signed_in_at"])
    except (ValueError, TypeError, KeyError):
        return False
    return 0 <= age < ttl


def _is_locked(name: str, now: float) -> tuple[bool, int]:
    with _FAILURE_LOCK:
        attempts = [t for t in _FAILURES.get(name, []) if (now - t) < FAILURE_WINDOW_SEC]
        _FAILURES[name] = attempts
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            return True, max(1, int((FAILURE_WINDOW_SEC - (now - attempts[0])) / 60) + 1)
    return False, 0


def _record_failure(name: str, now: float):
    with _FAILURE_LOCK:
        _FAILURES.setdefault(name, []).append(now)


def authenticate_for_test(username: str, password: str, users: Mapping[str, Mapping]) -> bool:
    """Pure credential check used in tests; UI uses the same verifier."""
    name = str(username).strip().lower()
    entry = users.get(name)
    stored = entry.get("password_hash", "") if entry and entry.get("enabled") else ""
    verified = check_password(password, stored)
    return bool(entry and entry.get("enabled") and verified)


def require_login(*, show_logout: bool = True) -> str:
    """Fail-closed gate, called before any dashboard import or data read."""
    import streamlit as st

    settings = _load_auth_settings(st)
    if settings is None:
        st.error("Client access has not been configured. Contact the dashboard administrator.")
        st.stop()
    users, ttl = settings

    identity = st.session_state.get(_STATE_KEY)
    if _session_valid(identity, users, ttl):
        username = identity["username"]
        if show_logout:
            with st.sidebar:
                st.caption(f"Signed in: **{username}**")
                if st.button("Sign out", key="_mv_signout_button"):
                    st.session_state.pop(_STATE_KEY, None)
                    st.rerun()
        return username

    st.session_state.pop(_STATE_KEY, None)
    st.set_page_config(page_title="MarketVector • Client Sign In", page_icon="🔒", layout="centered", initial_sidebar_state="collapsed")
    st.markdown("<style>section[data-testid='stSidebar']{display:none}</style>", unsafe_allow_html=True)
    st.title("🔒 MarketVector Client Access")
    st.write("Enter your assigned username and password to view the dashboards.")

    with st.form("_mv_client_login_form", clear_on_submit=True):
        name = st.text_input("Username", max_chars=32, autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    if submitted:
        normalized = str(name).strip().lower()
        if not _USERNAME_RE.fullmatch(normalized):
            normalized = "_invalid_account"
        now = time.monotonic()
        locked, minutes = _is_locked(normalized, now)
        if locked:
            st.error(f"Too many unsuccessful attempts. Try again in about {minutes} minute(s).")
        elif authenticate_for_test(normalized, password, users):
            with _FAILURE_LOCK:
                _FAILURES.pop(normalized, None)
            st.session_state[_STATE_KEY] = {
                "username": normalized,
                "signed_in_at": time.time(),
                "password_hash_fingerprint": hashlib.sha256(users[normalized]["password_hash"].encode()).hexdigest(),
            }
            st.rerun()
        else:
            _record_failure(normalized, now)
            st.error("Incorrect username or password.")
    st.caption("Accounts are managed by the dashboard administrator. Do not share your login.")
    st.stop()
