"""Accounts: sign-up, sign-in (password, email link, Google), sessions, the admin page, and how each
reader connects Claude.

Claude connection, and why it has two kinds:
- "api": the reader's own Anthropic API key, billed to them. Any reader can use this. The key is stored
  encrypted and never sent back to the browser.
- "claude_code": the Claude Code login of the machine this server runs on. Only the owner of this
  instance may use it. Anthropic does not allow an app to collect or route other people's Claude
  subscription logins (code.claude.com/docs/en/legal-and-compliance), so other readers use a key, or
  run their own copy of the app. The app never sees the login itself: the owner signs in with
  `claude auth login` in a terminal, through Anthropic's own flow, and the app only reads the status.

Email: with SMTP_HOST set, links are sent by email. Without it they are printed in the server log, so
a self-hosted instance works with no mail server.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import llm
import search
import storage
from db import pool, q

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
MAIL_FROM = os.environ.get("MAIL_FROM", "Reading Companion <no-reply@localhost>")
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:5173").rstrip("/")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "").strip().lower()
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "1") == "1"
# Old local mode: an email alone logs in, with no check. Only for development on localhost.
INSECURE_DEV_LOGIN = os.environ.get("INSECURE_DEV_LOGIN") == "1"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

SESSION_DAYS = 30
LINK_MINUTES = {"login": 15, "verify": 24 * 60, "reset": 30}
LINKS_PER_EMAIL = 5      # unused links one email may have at a time
MAX_FAILURES = 10        # wrong passwords per email in FAILURE_WINDOW_S before sign-in is paused
MAX_FAILURES_IP = 50     # the same, per address
FAILURE_WINDOW_S = 15 * 60

router = APIRouter(prefix="/api")


# ---- small helpers -----------------------------------------------------------

def sha(token: str) -> str:
    """Session and link tokens are saved only as a hash. A leaked database cannot be used to sign in."""
    return hashlib.sha256(token.encode()).hexdigest()


def clean_email(email: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
        raise HTTPException(422, "Please type a valid email")
    return email


# scrypt from the standard library: slow on purpose (about 0.1 s and 32 MB per check), so a stolen
# database cannot be guessed through fast.
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 * 1024 * 1024, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    # A missing account is checked against a dummy hash, so the answer takes as long and
    # does not tell whether the email has an account.
    _, n, r, p, salt, h = (stored or DUMMY_HASH).split("$")
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                         maxmem=64 * 1024 * 1024)
    return stored is not None and hmac.compare_digest(got.hex(), h)


DUMMY_HASH = hash_password(secrets.token_hex(8))

# ponytail: failed sign-ins are counted in memory, so a restart or a second server process resets them.
# Move to the database (or Redis) when the app runs as more than one process.
_failures: dict[str, list[float]] = defaultdict(list)


def paused(key: str, limit: int) -> bool:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < FAILURE_WINDOW_S]
    return len(_failures[key]) >= limit


def client_ip(request: Request) -> str:
    # Behind a proxy (Fly.io, Render, a load balancer) the real address is in X-Forwarded-For.
    # ponytail: trusts the first entry. Fine behind one known proxy; a spoofed header only lets an
    # attacker spread their own failures over fake addresses, the per-email limit still holds.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "")


def send_mail(to: str, subject: str, text: str) -> None:
    if not SMTP_HOST:
        print(f"\n--- MAIL (SMTP_HOST is not set, so it is shown here) ---\nTo: {to}\nSubject: {subject}\n\n{text}\n---\n",
              file=sys.stderr, flush=True)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = MAIL_FROM, to, subject
    msg.set_content(text)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise HTTPException(502, "Could not send the email. Try again later.") from e


def make_link(email: str, purpose: str) -> str:
    """A one-time link token. Only its hash is saved."""
    q("DELETE FROM login_tokens WHERE expires_at < now()")
    if q("SELECT count(*) AS n FROM login_tokens WHERE email = %s", (email,))[0]["n"] >= LINKS_PER_EMAIL:
        raise HTTPException(429, "Too many links for this email. Use the last one we sent, or wait a while.")
    token = secrets.token_urlsafe(32)
    q("INSERT INTO login_tokens (token_hash, email, purpose, expires_at) "
      "VALUES (%s, %s, %s, now() + make_interval(mins => %s))", (sha(token), email, purpose, LINK_MINUTES[purpose]))
    return token


def use_link(token: str, purpose: str) -> str | None:
    """The email the link was for, or None. A link works once."""
    rows = q("DELETE FROM login_tokens WHERE token_hash = %s AND purpose = %s AND expires_at > now() RETURNING email",
             (sha(token), purpose))
    return rows[0]["email"] if rows else None


def mail_link(email: str, purpose: str, path: str) -> None:
    token = make_link(email, purpose)
    url = f"{APP_URL}{path}{'&' if '?' in path else '?'}token={token}"
    minutes = LINK_MINUTES[purpose]
    valid = f"{minutes // 60} hours" if minutes >= 120 else f"{minutes} minutes"
    subject, intro = {
        "verify": ("Confirm your email for Reading Companion", "Open this link to confirm your email and finish signing up."),
        "login": ("Your Reading Companion sign-in link", "Open this link to sign in."),
        "reset": ("Reset your Reading Companion password", "Open this link to choose a new password."),
    }[purpose]
    send_mail(email, subject, f"{intro} It works once, for {valid}.\n\n{url}\n\n"
                              "If you did not ask for this, you can ignore this email.")


# ---- users and sessions --------------------------------------------------------

USER_COLS = "id, email, name, role, disabled, email_verified, (password_hash IS NOT NULL) AS has_password, " \
            "(google_sub IS NOT NULL) AS has_google, created_at"


def public(user: dict) -> dict:
    return {k: user[k] for k in ("id", "email", "name", "role", "email_verified", "has_password", "has_google")}


def user_by_email(email: str) -> dict | None:
    rows = q(f"SELECT {USER_COLS}, password_hash FROM users WHERE email = %s", (email,))
    return rows[0] if rows else None


def may_register(email: str) -> bool:
    return ALLOW_REGISTRATION or email == OWNER_EMAIL or not q("SELECT 1 FROM users LIMIT 1")


def create_user(email: str, name: str = "", password_hash: str | None = None, verified: bool = False,
                google_sub: str | None = None) -> dict:
    """The owner is OWNER_EMAIL, or, when that is not set, the very first account on a new install."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")  # two first sign-ups cannot both become owner
        first = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM users) AS first").fetchone()["first"]
        owner = email == OWNER_EMAIL if OWNER_EMAIL else first
        return conn.execute(
            f"INSERT INTO users (email, name, password_hash, email_verified, google_sub, role) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {USER_COLS}",
            (email, name, password_hash, verified, google_sub, "owner" if owner else "user")).fetchone()


def setup() -> None:
    """At start: OWNER_EMAIL decides who the owner is. Warn about settings that matter."""
    if OWNER_EMAIL:
        q("UPDATE users SET role = 'admin' WHERE role = 'owner' AND email <> %s", (OWNER_EMAIL,))
        q("UPDATE users SET role = 'owner', disabled = false WHERE email = %s", (OWNER_EMAIL,))
    elif q("SELECT 1 FROM users LIMIT 1") and not q("SELECT 1 FROM users WHERE role = 'owner'"):
        print("WARNING: this instance has accounts but no owner. Set OWNER_EMAIL to your email and restart.",
              file=sys.stderr)
    if INSECURE_DEV_LOGIN:
        print("WARNING: INSECURE_DEV_LOGIN=1. Anyone can sign in as any email. Keep this on localhost.", file=sys.stderr)
    if not SMTP_HOST:
        print("NOTE: SMTP_HOST is not set. Sign-in and confirmation links are printed in this log.", file=sys.stderr)
    _fernet()  # makes the secret file now, not during the first request


def start_session(response: Response, user_id: int, request: Request | None = None) -> None:
    token = secrets.token_urlsafe(32)
    q("DELETE FROM sessions WHERE expires_at < now()")
    q("INSERT INTO sessions (token, user_id, expires_at, user_agent) "
      "VALUES (%s, %s, now() + make_interval(days => %s), %s)",
      (sha(token), user_id, SESSION_DAYS, (request.headers.get("user-agent", "") if request else "")[:200]))
    response.set_cookie("session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=APP_URL.startswith("https"))


def current_user(session: str | None = Cookie(None)) -> dict:
    rows = q(f"SELECT u.id, u.email, u.name, u.role, u.disabled, u.email_verified, "
             f"(u.password_hash IS NOT NULL) AS has_password, (u.google_sub IS NOT NULL) AS has_google "
             f"FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = %s AND s.expires_at > now()",
             (sha(session or ""),))
    if not rows or rows[0]["disabled"]:
        raise HTTPException(401, "Not signed in")
    return rows[0]


def admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("owner", "admin"):
        raise HTTPException(403, "Only an admin can do this")
    return user


def delete_user_data(user_id: int, email: str) -> None:
    """Remove a reader and everything that belongs to them: rows, search index, files."""
    search.delete("user_id", user_id)
    q("DELETE FROM login_tokens WHERE email = %s", (email,))
    q("DELETE FROM users WHERE id = %s", (user_id,))  # every other table follows by ON DELETE CASCADE
    storage.delete(f"users/{user_id}")


def back_to_app(error: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"{APP_URL}/" + (f"?error={error}" if error else ""), status_code=303)


# ---- sign-up and sign-in ---------------------------------------------------------

@router.get("/auth/options")
def auth_options():
    """What the sign-in page should offer."""
    return {"google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET), "registration": ALLOW_REGISTRATION,
            "mail": bool(SMTP_HOST), "dev_login": INSECURE_DEV_LOGIN}


class Register(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=100)


@router.post("/register")
def register(body: Register, request: Request, response: Response):
    """The answer is the same whether or not the email has an account, so nobody can test emails here."""
    email = clean_email(body.email)
    if paused(f"ip:{client_ip(request)}", MAX_FAILURES_IP):
        raise HTTPException(429, "Too many tries. Wait 15 minutes.")
    _failures[f"ip:{client_ip(request)}"].append(time.time())  # every sign-up counts, so this cannot be used to spam
    if user_by_email(email):
        mail_link(email, "reset", "/?reset=1")  # the real owner of the email learns how to get in
        return {"sent": True}
    if not may_register(email):
        raise HTTPException(403, "New accounts are closed on this server. Ask the admin.")
    user = create_user(email, body.name.strip(), hash_password(body.password), verified=INSECURE_DEV_LOGIN)
    if INSECURE_DEV_LOGIN:
        start_session(response, user["id"], request)
        return public(user)
    mail_link(email, "verify", "/api/verify")
    return {"sent": True}


@router.get("/verify")
def verify_email(token: str, request: Request):
    email = use_link(token, "verify")
    user = user_by_email(email) if email else None
    if not user or user["disabled"]:
        return back_to_app("link")
    q("UPDATE users SET email_verified = true WHERE id = %s", (user["id"],))
    response = back_to_app()
    start_session(response, user["id"], request)
    return response


class PasswordLogin(BaseModel):
    email: str
    password: str = Field(max_length=200)


@router.post("/login/password")
def login_password(body: PasswordLogin, request: Request, response: Response):
    email = clean_email(body.email)
    keys = [(f"email:{email}", MAX_FAILURES), (f"ip:{client_ip(request)}", MAX_FAILURES_IP)]
    if any(paused(k, limit) for k, limit in keys):
        raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes, or sign in with an email link.")
    user = user_by_email(email)
    if not check_password(body.password, user and user["password_hash"]):
        for k, _ in keys:
            _failures[k].append(time.time())
        raise HTTPException(401, "Wrong email or password")
    if user["disabled"]:
        raise HTTPException(403, "This account is turned off. Ask the admin.")
    if not user["email_verified"]:
        mail_link(email, "verify", "/api/verify")
        raise HTTPException(403, "Confirm your email first. We sent you a new link.")
    _failures.pop(f"email:{email}", None)
    start_session(response, user["id"], request)
    return public(user)


class EmailOnly(BaseModel):
    email: str


@router.post("/login/link")
def login_link(body: EmailOnly, request: Request, response: Response):
    email = clean_email(body.email)
    if INSECURE_DEV_LOGIN:  # local development only: no check at all
        user = user_by_email(email) or create_user(email, verified=True)
        start_session(response, user["id"], request)
        return public(user)
    if user_by_email(email) or may_register(email):
        mail_link(email, "login", "/api/login/verify")
    return {"sent": True}


@router.get("/login/verify")
def verify_login(token: str, request: Request):
    """The link from the email. It works once."""
    # ponytail: a mail scanner that opens links can use the link up before the reader does.
    # If that happens, change this to a page with a "Sign in" button that POSTs the token.
    email = use_link(token, "login")
    if not email:
        return back_to_app("link")
    user = user_by_email(email)
    if not user:
        if not may_register(email):
            return back_to_app("closed")
        user = create_user(email, verified=True)
    if user["disabled"]:
        return back_to_app("disabled")
    q("UPDATE users SET email_verified = true WHERE id = %s", (user["id"],))
    response = back_to_app()
    start_session(response, user["id"], request)
    return response


@router.post("/password/forgot")
def forgot_password(body: EmailOnly):
    email = clean_email(body.email)
    if user_by_email(email):
        mail_link(email, "reset", "/?reset=1")
    return {"sent": True}


class Reset(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=200)


@router.post("/password/reset")
def reset_password(body: Reset, request: Request, response: Response):
    email = use_link(body.token, "reset")
    user = user_by_email(email) if email else None
    if not user:
        raise HTTPException(400, "This link is not valid any more. Ask for a new one.")
    if user["disabled"]:
        raise HTTPException(403, "This account is turned off. Ask the admin.")
    q("UPDATE users SET password_hash = %s, email_verified = true WHERE id = %s",
      (hash_password(body.password), user["id"]))
    q("DELETE FROM sessions WHERE user_id = %s", (user["id"],))  # whoever knew the old password is out
    _failures.pop(f"email:{email}", None)
    start_session(response, user["id"], request)
    return public({**user, "has_password": True, "email_verified": True})


# ---- Google ------------------------------------------------------------------------
# Authorization code flow with PKCE. The state and verifier live in a short cookie on this browser.

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_CALLBACK = "/api/auth/google/callback"


@router.get("/auth/google")
def google_start():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(404, "Google sign-in is not set up on this server")
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = RedirectResponse(GOOGLE_AUTH + "?" + urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": APP_URL + GOOGLE_CALLBACK, "response_type": "code",
        "scope": "openid email profile", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "prompt": "select_account"}), status_code=303)
    response.set_cookie("google_oauth", f"{state}.{verifier}", max_age=600, httponly=True, samesite="lax",
                        secure=APP_URL.startswith("https"), path="/api/auth/google")
    return response


def google_profile(code: str, verifier: str) -> dict:
    """Swap the code for a token, then read who signed in. Talks to Google only, over HTTPS."""
    data = urllib.parse.urlencode({
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": APP_URL + GOOGLE_CALLBACK, "grant_type": "authorization_code",
        "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request(GOOGLE_TOKEN, data), timeout=10) as r:
        access = json.load(r)["access_token"]
    req = urllib.request.Request(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", google_oauth: str | None = Cookie(None)):
    saved_state, _, verifier = (google_oauth or "").partition(".")
    if not code or not saved_state or not hmac.compare_digest(saved_state, state):
        return back_to_app("google")
    try:
        profile = google_profile(code, verifier)
    except (OSError, ValueError, KeyError):
        return back_to_app("google")
    email = (profile.get("email") or "").lower()
    if not profile.get("sub") or not email or not profile.get("email_verified"):
        return back_to_app("google")
    rows = q(f"SELECT {USER_COLS} FROM users WHERE google_sub = %s", (profile["sub"],))
    user = rows[0] if rows else user_by_email(email)
    if user is None:
        if not may_register(email):
            return back_to_app("closed")
        user = create_user(email, (profile.get("name") or "")[:100], verified=True, google_sub=profile["sub"])
    if user["disabled"]:
        return back_to_app("disabled")
    # Google checked the email, so an account with the same email may be linked to this Google login.
    q("UPDATE users SET google_sub = %s, email_verified = true WHERE id = %s", (profile["sub"], user["id"]))
    response = back_to_app()
    response.delete_cookie("google_oauth", path="/api/auth/google")
    start_session(response, user["id"], request)
    return response


# ---- my account --------------------------------------------------------------------------

@router.get("/me")
def me(user: dict = Depends(current_user)):
    """connection: "api", "claude_code", or None when the reader still has to connect Claude."""
    return {**public(user), "connection": (connection(user) or {}).get("provider")}


class Profile(BaseModel):
    name: str = Field(max_length=100)


@router.put("/me")
def update_me(body: Profile, user: dict = Depends(current_user)):
    q("UPDATE users SET name = %s WHERE id = %s", (body.name.strip(), user["id"]))
    return public({**user, "name": body.name.strip()})


class NewPassword(BaseModel):
    current: str = Field(default="", max_length=200)
    new: str = Field(min_length=8, max_length=200)


@router.put("/me/password")
def change_password(body: NewPassword, session: str | None = Cookie(None), user: dict = Depends(current_user)):
    """An account made by email link or Google can set its first password without a current one."""
    if user["has_password"]:
        key = f"email:{user['email']}"
        if paused(key, MAX_FAILURES):
            raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes.")
        if not check_password(body.current, user_by_email(user["email"])["password_hash"]):
            _failures[key].append(time.time())
            raise HTTPException(403, "Your current password is not right")
    q("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(body.new), user["id"]))
    q("DELETE FROM sessions WHERE user_id = %s AND token <> %s", (user["id"], sha(session or "")))
    return {}


@router.post("/logout")
def logout(response: Response, session: str | None = Cookie(None)):
    q("DELETE FROM sessions WHERE token = %s", (sha(session or ""),))
    response.delete_cookie("session")
    return {}


@router.post("/logout/others")
def logout_others(session: str | None = Cookie(None), user: dict = Depends(current_user)):
    """Sign out on every other device and browser."""
    n = len(q("DELETE FROM sessions WHERE user_id = %s AND token <> %s RETURNING 1", (user["id"], sha(session or ""))))
    return {"signed_out": n}


@router.delete("/me")
def delete_account(response: Response, user: dict = Depends(current_user)):
    if user["role"] == "owner":
        raise HTTPException(409, "The owner's account cannot be deleted here. Make someone else the owner first "
                                 "(OWNER_EMAIL), or remove the whole instance.")
    delete_user_data(user["id"], user["email"])
    response.delete_cookie("session")
    return {}


# ---- admin -----------------------------------------------------------------------------

@router.get("/admin/users")
def list_users(_: dict = Depends(admin)):
    return q("SELECT u.id, u.email, u.name, u.role, u.disabled, u.email_verified, u.created_at, "
             "(u.password_hash IS NOT NULL) AS has_password, (u.google_sub IS NOT NULL) AS has_google, "
             "(SELECT count(*) FROM books b WHERE b.user_id = u.id) AS books, "
             "(SELECT coalesce(sum(tokens_in + tokens_out), 0) FROM usage_log l WHERE l.user_id = u.id)::bigint AS tokens, "
             "(SELECT max(created_at) FROM sessions s WHERE s.user_id = u.id) AS last_sign_in, "
             "(SELECT provider FROM llm_connections c WHERE c.user_id = u.id) AS connection "
             "FROM users u ORDER BY u.id")


class UserChange(BaseModel):
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None


def target_user(user_id: int, me_: dict) -> dict:
    rows = q("SELECT id, email, role FROM users WHERE id = %s", (user_id,))
    if not rows:
        raise HTTPException(404, "No such account")
    if rows[0]["role"] == "owner":
        raise HTTPException(403, "The owner cannot be changed here. The owner is set with OWNER_EMAIL.")
    if rows[0]["id"] == me_["id"]:
        raise HTTPException(403, "You cannot change your own account here")
    return rows[0]


@router.put("/admin/users/{user_id}")
def change_user(user_id: int, body: UserChange, me_: dict = Depends(admin)):
    target = target_user(user_id, me_)
    if body.role is not None:
        if me_["role"] != "owner":
            raise HTTPException(403, "Only the owner can make or remove admins")
        q("UPDATE users SET role = %s WHERE id = %s", (body.role, user_id))
    if body.disabled is not None:
        if target["role"] == "admin" and me_["role"] != "owner":
            raise HTTPException(403, "Only the owner can turn off an admin")
        q("UPDATE users SET disabled = %s WHERE id = %s", (body.disabled, user_id))
        if body.disabled:
            q("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    return {}


@router.delete("/admin/users/{user_id}")
def remove_user(user_id: int, me_: dict = Depends(admin)):
    target = target_user(user_id, me_)
    if target["role"] == "admin" and me_["role"] != "owner":
        raise HTTPException(403, "Only the owner can remove an admin")
    delete_user_data(target["id"], target["email"])
    return {}


# ---- Claude connection -----------------------------------------------------------------

SECRET_FILE = storage.DATA / "secret.key"


def _fernet() -> Fernet:
    """API keys are encrypted with APP_SECRET. Without it, a random secret is made once and kept in
    the data folder. Losing the secret only means readers must add their keys again."""
    secret = os.environ.get("APP_SECRET")
    if not secret:
        if not SECRET_FILE.exists():
            SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            SECRET_FILE.write_text(secrets.token_urlsafe(32))
            print(f"NOTE: APP_SECRET is not set. Made one in {SECRET_FILE}. Keep that file, or set APP_SECRET.",
                  file=sys.stderr)
        secret = SECRET_FILE.read_text().strip()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def claude_code_installed() -> bool:
    return shutil.which(llm.CLAUDE) is not None


def may_use_claude_code(user: dict) -> bool:
    return user["role"] == "owner" and claude_code_installed()


def claude_code_status() -> dict:
    """Who Claude Code on this machine is signed in as. Only the status: the app never reads the login."""
    try:
        out = subprocess.run([llm.CLAUDE, "auth", "status"], capture_output=True, encoding="utf-8", timeout=20,
                             cwd=tempfile.gettempdir())
        status = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"logged_in": False}
    return {"logged_in": bool(status.get("loggedIn")), "email": status.get("email"),
            "plan": status.get("subscriptionType"), "org": status.get("orgName")}


def connection(user: dict) -> dict | None:
    """The reader's saved choice. The owner without one uses Claude Code on this machine."""
    rows = q("SELECT provider, api_key_enc, key_hint FROM llm_connections WHERE user_id = %s", (user["id"],))
    if rows:
        return rows[0]
    return {"provider": "claude_code", "api_key_enc": None, "key_hint": None} if may_use_claude_code(user) else None


# ponytail: one LLM call at a time per account, in this process. A reader waits only for their own calls.
_llm_locks: dict[int, threading.Lock] = {}


@contextmanager
def llm_session(user: dict):
    """Run LLM calls for this reader with their own connection. Every LLM call in the app goes through here."""
    conn = connection(user)
    if conn is None:
        raise HTTPException(409, "Connect Claude first: open Settings and add your Anthropic API key.")
    if conn["provider"] == "claude_code":
        if not may_use_claude_code(user):
            raise HTTPException(409, "Claude Code on this server is only for its owner. Add your own API key in Settings.")
        use = {"provider": "claude_code"}
    else:
        try:
            use = {"provider": "api", "api_key": _fernet().decrypt(conn["api_key_enc"].encode()).decode()}
        except InvalidToken:
            raise HTTPException(409, "Your saved API key cannot be read (the server secret changed). "
                                     "Add it again in Settings.") from None
    with _llm_locks.setdefault(user["id"], threading.Lock()), llm.using(use):
        yield


@router.get("/connection")
def get_connection(user: dict = Depends(current_user)):
    conn = connection(user)
    owner = may_use_claude_code(user)
    return {"provider": conn and conn["provider"],
            "key_hint": conn and conn["key_hint"],
            "has_key": bool(conn and conn["api_key_enc"]),
            "claude_code": {"allowed": owner, "status": claude_code_status() if owner else None}}


class ApiKey(BaseModel):
    key: str = Field(min_length=20, max_length=300)


def check_key(key: str) -> None:
    """One tiny request that costs nothing: list one model. Shows the key works before it is saved."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=key, max_retries=0, timeout=15).models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise HTTPException(400, "Anthropic did not accept this key. Check that you copied all of it.") from None
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach Anthropic to check the key. Try again.") from None
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Anthropic answered with an error ({e.status_code}). Try again later.") from None


@router.put("/connection/key")
def save_key(body: ApiKey, user: dict = Depends(current_user)):
    key = body.key.strip()
    if not key.startswith("sk-ant-") or re.search(r"\s", key):
        raise HTTPException(422, "An Anthropic API key starts with sk-ant-. Copy it from console.anthropic.com.")
    check_key(key)
    q("INSERT INTO llm_connections (user_id, provider, api_key_enc, key_hint) VALUES (%s, 'api', %s, %s) "
      "ON CONFLICT (user_id) DO UPDATE SET provider = 'api', api_key_enc = EXCLUDED.api_key_enc, "
      "key_hint = EXCLUDED.key_hint, updated_at = now()",
      (user["id"], _fernet().encrypt(key.encode()).decode(), key[-4:]))
    return get_connection(user)


@router.delete("/connection/key")
def remove_key(user: dict = Depends(current_user)):
    q("DELETE FROM llm_connections WHERE user_id = %s", (user["id"],))
    return get_connection(user)


class Provider(BaseModel):
    provider: Literal["api", "claude_code"]


@router.put("/connection/provider")
def choose_provider(body: Provider, user: dict = Depends(current_user)):
    """The owner can switch between their saved key and Claude Code on this machine."""
    if body.provider == "claude_code" and not may_use_claude_code(user):
        raise HTTPException(403, "Claude Code on this server is only for its owner")
    conn = q("SELECT api_key_enc FROM llm_connections WHERE user_id = %s", (user["id"],))
    if body.provider == "api" and not (conn and conn[0]["api_key_enc"]):
        raise HTTPException(409, "Add an API key first")
    q("INSERT INTO llm_connections (user_id, provider) VALUES (%s, %s) "
      "ON CONFLICT (user_id) DO UPDATE SET provider = EXCLUDED.provider, updated_at = now()",
      (user["id"], body.provider))
    return get_connection(user)
