"""Reading Companion backend: login, upload, page view, cached page cards, search and chat.

Run from this folder:  uvicorn app:app --port 8000
Without SMTP_HOST the login has no check, so the app must then only listen on localhost.
"""
import hashlib
import os
import re
import secrets
import smtplib
import sys
import threading
import time
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

import llm
import reader_profile as profile
import search
import storage
from parsing import chapters, figure_pages, page_image, parse_pdf, toc

HERE = Path(__file__).parent
# Login by magic link is on when SMTP_HOST is set. APP_URL is where the reader opens the app.
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
MAIL_FROM = os.environ.get("MAIL_FROM", "Reading Companion <no-reply@localhost>")
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:5173").rstrip("/")
LINK_MINUTES = 15
LINKS_PER_EMAIL = 5  # unused links one email may have at a time
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://reader:reader@127.0.0.1:5433/reader")  # not "localhost": it tries IPv6 first and hangs
MAX_UPLOAD = 500 * 1024 * 1024
PREMAKE_PAGES = int(os.environ.get("PREMAKE_PAGES", "2"))  # cards made ahead of the reader. 0 turns it off.
PREMAKE_AFTER_S = 5   # the reader must still be on the page after this long before the next cards are made
MIN_PAGE_CHARS = 40  # below this the page has no real text (blank or scanned)

# check: test a connection before using it. After the laptop sleeps, the saved connections are dead;
# without this the first requests after waking up fail.
pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row, "autocommit": True}, open=False,
                      check=ConnectionPool.check_connection)
# ponytail: one LLM call at a time for the whole app. Make it per account when there are many users.
llm_lock = threading.Lock()


def q(sql: str, params: tuple = ()) -> list[dict]:
    with pool.connection() as conn:
        cur = conn.execute(sql, params or None)  # None: a "%" in SQL without parameters is plain text
        return cur.fetchall() if cur.description else []


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=15)
    q((HERE / "schema.sql").read_text())
    if not SMTP_HOST:
        print("WARNING: SMTP_HOST is not set. Anyone can log in as any email. Keep this on localhost.", file=sys.stderr)
    # A crash during parsing leaves books in 'parsing'. Start those again.
    for b in q("SELECT id, file_path FROM books WHERE status = 'parsing' AND file_path <> ''"):
        threading.Thread(target=ingest, args=(b["id"], b["file_path"]), daemon=True).start()
    # Same for the search index. One thread, one book after another: they share the GPU.
    unindexed = [b["id"] for b in q("SELECT id FROM books WHERE status = 'ready' AND index_status <> 'ready'")]
    threading.Thread(target=lambda: [index_job(i) for i in unindexed], daemon=True).start()
    yield
    pool.close()


app = FastAPI(lifespan=lifespan)


# ---- users and login -------------------------------------------------------

class Login(BaseModel):
    email: str


def sha(token: str) -> str:
    """Session and link tokens are saved only as a hash. A leaked database cannot be used to log in."""
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(session: str | None = Cookie(None)) -> dict:
    rows = q("SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
             "WHERE s.token = %s AND s.expires_at > now()", (sha(session or ""),))
    if not rows:
        raise HTTPException(401, "Not logged in")
    return rows[0]


def start_session(response: Response, email: str) -> dict:
    """Create the user if new, and set the session cookie."""
    user = q("INSERT INTO users (email) VALUES (%s) ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email "
             "RETURNING id, email", (email,))[0]
    token = secrets.token_urlsafe(32)
    q("DELETE FROM sessions WHERE expires_at < now()")
    q("INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, now() + interval '30 days')",
      (sha(token), user["id"]))
    response.set_cookie("session", token, max_age=30 * 86400, httponly=True, samesite="lax",
                        secure=APP_URL.startswith("https"))
    return user


def send_mail(to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = MAIL_FROM, to, subject
    msg.set_content(text)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASSWORD or "")
        smtp.send_message(msg)


@app.post("/api/login")
def login(body: Login, response: Response):
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
        raise HTTPException(422, "Please type a valid email")
    if not SMTP_HOST:  # local use: no check
        return start_session(response, email)

    q("DELETE FROM login_tokens WHERE expires_at < now()")
    if q("SELECT count(*) AS n FROM login_tokens WHERE email = %s", (email,))[0]["n"] >= LINKS_PER_EMAIL:
        raise HTTPException(429, "Too many login links. Use the last one we sent, or wait 15 minutes.")
    token = secrets.token_urlsafe(32)
    q("INSERT INTO login_tokens (token_hash, email, expires_at) VALUES (%s, %s, now() + make_interval(mins => %s))",
      (sha(token), email, LINK_MINUTES))
    try:
        send_mail(email, "Your Reading Companion login link",
                  f"Open this link to log in. It works once, for {LINK_MINUTES} minutes.\n\n"
                  f"{APP_URL}/api/login/verify?token={token}\n\n"
                  "If you did not ask for this, you can ignore this email.")
    except (OSError, smtplib.SMTPException) as e:
        q("DELETE FROM login_tokens WHERE token_hash = %s", (sha(token),))
        raise HTTPException(502, "Could not send the login email. Try again later.") from e
    return {"sent": True}


@app.get("/api/login/verify")
def verify_login(token: str):
    """The link from the email. It works once."""
    # ponytail: a mail scanner that opens links can use the link up before the reader does.
    # If that happens, change this to a page with a "Log in" button that POSTs the token.
    rows = q("DELETE FROM login_tokens WHERE token_hash = %s AND expires_at > now() RETURNING email", (sha(token),))
    if not rows:
        raise HTTPException(400, "This login link is not valid any more. Ask for a new one.")
    response = RedirectResponse(APP_URL + "/", status_code=303)
    start_session(response, rows[0]["email"])
    return response


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


@app.post("/api/logout")
def logout(response: Response, session: str | None = Cookie(None)):
    q("DELETE FROM sessions WHERE token = %s", (sha(session or ""),))
    response.delete_cookie("session")
    return {}


@app.get("/api/export")
def export_data(response: Response, user: dict = Depends(current_user)):
    """Everything the tool saved about the reader, as one JSON file. The books themselves are not included."""
    uid = (user["id"],)
    response.headers["Content-Disposition"] = 'attachment; filename="reading-companion-export.json"'
    return {
        "user": q("SELECT email, created_at FROM users WHERE id = %s", uid)[0],
        "skills": q("SELECT skill, level, source, updated_at FROM reader_skills WHERE user_id = %s ORDER BY skill", uid),
        "skill_history": q("SELECT skill, old_level, new_level, source, changed_at FROM skill_history "
                           "WHERE user_id = %s ORDER BY id", uid),
        "skill_evidence": q("SELECT skill, signal, book_id, page_no, created_at FROM skill_evidence "
                            "WHERE user_id = %s ORDER BY id", uid),
        "books": q(f"SELECT {BOOK_COLS} FROM books WHERE user_id = %s ORDER BY id", uid),
        "chats": q("SELECT id, book_id, title, created_at FROM chats WHERE user_id = %s ORDER BY id", uid),
        "highlights": q("SELECT h.book_id, h.page_no, h.color, h.text, h.created_at FROM highlights h "
                        "JOIN books b ON b.id = h.book_id WHERE b.user_id = %s ORDER BY h.id", uid),
        "messages": q("SELECT m.chat_id, m.role, m.text, m.page_no, m.source_pages, m.created_at FROM messages m "
                      "JOIN chats c ON c.id = m.chat_id WHERE c.user_id = %s ORDER BY m.id", uid),
        "usage": q("SELECT feature, model, tokens_in, tokens_out, ms, created_at FROM usage_log "
                   "WHERE user_id = %s ORDER BY id", uid),
    }


@app.delete("/api/me")
def delete_account(response: Response, user: dict = Depends(current_user)):
    """Remove the reader and everything that belongs to them: rows, search index, files."""
    search.delete("user_id", user["id"])
    q("DELETE FROM login_tokens WHERE email = %s", (user["email"],))
    q("DELETE FROM users WHERE id = %s", (user["id"],))  # every other table follows by ON DELETE CASCADE
    storage.delete(f"users/{user['id']}")
    response.delete_cookie("session")
    return {}


# ---- books -----------------------------------------------------------------

BOOK_COLS = "id, title, status, index_status, error, page_count, last_page, created_at"


def get_book(book_id: int, user: dict) -> dict:
    rows = q("SELECT * FROM books WHERE id = %s AND user_id = %s", (book_id, user["id"]))
    if not rows:
        raise HTTPException(404, "Book not found")
    book = rows[0]
    if book["file_path"]:
        book["file_path"] = storage.local(book["file_path"])  # the storage key becomes a real file path
    return book


def ingest(book_id: int, key: str) -> None:
    """Parse the book and save every page. Safe to run again after a crash."""
    try:
        pages = parse_pdf(storage.local(key))
        with pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM pages WHERE book_id = %s", (book_id,))
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO pages (book_id, page_no, text_md) VALUES (%s, %s, %s)",
                                [(book_id, n, text) for n, text in enumerate(pages, 1)])
            conn.execute("UPDATE books SET status = 'ready', error = NULL, page_count = %s WHERE id = %s",
                         (len(pages), book_id))
    except Exception as e:
        q("UPDATE books SET status = 'error', error = %s WHERE id = %s", (str(e)[:500], book_id))
        return
    index_job(book_id)


def index_job(book_id: int) -> None:
    """Chunk, embed and index a parsed book. No LLM. Safe to run again after a crash."""
    try:
        book = q("UPDATE books SET index_status = 'indexing' WHERE id = %s RETURNING user_id, file_path",
                 (book_id,))[0]
        pages = q("SELECT page_no, text_md FROM pages WHERE book_id = %s ORDER BY page_no", (book_id,))
        search.index_book(book["user_id"], book_id, [(p["page_no"], p["text_md"]) for p in pages],
                          toc(storage.local(book["file_path"])))
        q("UPDATE books SET index_status = 'ready' WHERE id = %s", (book_id,))
    except Exception as e:
        q("UPDATE books SET index_status = 'error', error = %s WHERE id = %s", (str(e)[:500], book_id))


@app.get("/api/books")
def list_books(user: dict = Depends(current_user)):
    return q(f"SELECT {BOOK_COLS} FROM books WHERE user_id = %s ORDER BY created_at DESC", (user["id"],))


@app.post("/api/books")
def upload_book(file: UploadFile, background: BackgroundTasks, user: dict = Depends(current_user)):
    user_dir = storage.DATA / "users" / str(user["id"])
    user_dir.mkdir(parents=True, exist_ok=True)
    tmp = user_dir / f"upload-{secrets.token_hex(8)}.tmp"
    digest, size, head = hashlib.sha256(), 0, b""
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                head = head or chunk[:1024]
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "File is larger than 500 MB")
                digest.update(chunk)
                out.write(chunk)
        if b"%PDF-" not in head:
            raise HTTPException(415, "Only PDF files work for now")

        title = Path(file.filename or "book").stem[:200] or "book"
        rows = q("INSERT INTO books (user_id, title, file_hash) VALUES (%s, %s, %s) "
                 "ON CONFLICT (user_id, file_hash) DO NOTHING RETURNING id",
                 (user["id"], title, digest.hexdigest()))
        if not rows:  # this reader already uploaded the same file
            return q(f"SELECT {BOOK_COLS} FROM books WHERE user_id = %s AND file_hash = %s",
                     (user["id"], digest.hexdigest()))[0]

        book_id = rows[0]["id"]
        key = storage.book_key(user["id"], book_id)
        try:
            storage.put(key, tmp)
        except Exception:
            q("DELETE FROM books WHERE id = %s", (book_id,))  # no book row without its file
            raise
        q("UPDATE books SET file_path = %s WHERE id = %s", (key, book_id))
        background.add_task(ingest, book_id, key)
        return q(f"SELECT {BOOK_COLS} FROM books WHERE id = %s", (book_id,))[0]
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/books/{book_id}")
def book_info(book_id: int, user: dict = Depends(current_user)):
    get_book(book_id, user)
    return q(f"SELECT {BOOK_COLS} FROM books WHERE id = %s", (book_id,))[0]


@app.get("/api/books/{book_id}/file")
def book_file(book_id: int, user: dict = Depends(current_user)):
    return FileResponse(get_book(book_id, user)["file_path"], media_type="application/pdf")


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, user: dict = Depends(current_user)):
    """Remove one book with its pages, cards, chats, search index and file. The reader profile stays."""
    get_book(book_id, user)
    search.delete("book_id", book_id)
    q("DELETE FROM books WHERE id = %s", (book_id,))
    storage.delete(f"users/{user['id']}/books/{book_id}")
    return {}


class LastPage(BaseModel):
    page: int


@app.put("/api/books/{book_id}/last_page")
def set_last_page(book_id: int, body: LastPage, user: dict = Depends(current_user)):
    q("UPDATE books SET last_page = %s WHERE id = %s AND user_id = %s", (max(1, body.page), book_id, user["id"]))
    return {}


# ---- page cards ------------------------------------------------------------

def page_text(book_id: int, page_no: int) -> str:
    rows = q("SELECT text_md FROM pages WHERE book_id = %s AND page_no = %s", (book_id, page_no))
    if not rows:
        raise HTTPException(404, "This page is not parsed yet")
    return rows[0]["text_md"]


def log_usage(user_id: int, feature: str, usage: dict) -> None:
    q("INSERT INTO usage_log (user_id, feature, model, tokens_in, tokens_out, ms) VALUES (%s, %s, %s, %s, %s, %s)",
      (user_id, feature, usage["model"], usage["tokens_in"], usage["tokens_out"], usage["ms"]))


def make_card(book: dict, page_no: int, user: dict, level: str | None = None, feature: str = "page_card",
              only_saved: bool = False) -> dict:
    """The page card, from the cache or made now. level=None: the reader profile decides.

    only_saved: never call the LLM. Returns {"not_made": True} when no card is saved for this page.
    """
    book_id = book["id"]
    text = page_text(book_id, page_no)
    if level is not None and level not in profile.LEVELS:
        raise HTTPException(422, "Unknown level")
    image = None
    if len(text.strip()) < MIN_PAGE_CHARS:
        image = page_image(book["file_path"], page_no)
        if image is None:
            return {"no_text": True}

    if only_saved:
        # A page with a saved card was tagged before the card was made. Not tagged = no card yet.
        tags = q("SELECT skills FROM pages WHERE book_id = %s AND page_no = %s", (book_id, page_no))[0]["skills"]
        if tags is None and text.strip():
            return {"not_made": True}
    states = skill_rows(user["id"], skills_of_page(book_id, page_no, text, user))
    auto_level = profile.page_level([s["level"] for s in states])
    use = level or auto_level

    def cached():
        rows = q("SELECT card_json FROM page_cards WHERE book_id = %s AND page_no = %s AND level = %s",
                 (book_id, page_no, use))
        if not rows:
            return None
        # Only the LLM output is saved. The skills are looked up fresh each time.
        return {**rows[0]["card_json"], "level": use, "auto_level": auto_level, "skills": states}

    if card := cached():
        return card
    if only_saved:
        return {"not_made": True}
    with llm_lock:
        if card := cached():  # another request made it while this one waited
            return card
        try:
            # A page with a figure is sent as a picture too. The text layer cannot show a diagram.
            image = image or page_image(book["file_path"], page_no)
            result, usage = llm.explain_page(book["title"], page_no, text, use, image)
        except llm.LLMError as e:
            raise HTTPException(502, str(e))  # a bad output is never saved
        q("INSERT INTO page_cards (book_id, page_no, level, card_json, model) VALUES (%s, %s, %s, %s, %s) "
          "ON CONFLICT DO NOTHING", (book_id, page_no, use, Jsonb(result.model_dump()), usage["model"]))
        log_usage(user["id"], feature, usage)
    return cached()


def premake(book: dict, page_no: int, user: dict) -> None:
    """Make the cards for the next pages while the reader is on this one, so they open at once."""
    for n in range(page_no + 1, page_no + 1 + PREMAKE_PAGES):
        # Only for a reader who stays. Someone flipping through pages has moved on by now,
        # and a card for a page they never open is wasted LLM usage.
        if n == page_no + 1:
            time.sleep(PREMAKE_AFTER_S)
        if q("SELECT last_page FROM books WHERE id = %s", (book["id"],))[0]["last_page"] != page_no:
            return
        try:
            make_card(book, n, user, feature="page_card_premade")
        except HTTPException:
            return  # the end of the book, or the LLM failed. The card is made when the reader opens the page.


@app.get("/api/books/{book_id}/pages/{page_no}/card")
def page_card(book_id: int, page_no: int, background: BackgroundTasks, level: str | None = None,
              only_saved: bool = False, ahead: bool = True, user: dict = Depends(current_user)):
    """level: set when the reader clicked Simpler or Too simple.

    Auto read off in the app means: only_saved=true when a page opens (no LLM call), and ahead=false
    when the reader presses Explain now (make this card, but not the next pages).
    """
    book = get_book(book_id, user)
    card = make_card(book, page_no, user, level, only_saved=only_saved)
    if ahead and not only_saved:
        background.add_task(premake, book, page_no, user)  # runs after the answer is sent
    return card


def level_of_page(user_id: int, book_id: int, page_no: int) -> tuple[list[str], str]:
    """The skills already tagged on a page (no LLM call here) and the level to explain it at."""
    rows = q("SELECT skills FROM pages WHERE book_id = %s AND page_no = %s", (book_id, page_no))
    tags = (rows[0]["skills"] if rows else None) or []
    return tags, profile.page_level([s["level"] for s in skill_rows(user_id, tags)])


# ---- selected text, page check, chapters, usage (phase 4) ------------------

class Selection(BaseModel):
    action: Literal["simplify", "explain", "define"]
    text: str = Field(min_length=1, max_length=2000)


@app.post("/api/books/{book_id}/pages/{page_no}/select")
def explain_selection(book_id: int, page_no: int, body: Selection, user: dict = Depends(current_user)):
    book = get_book(book_id, user)
    text = page_text(book_id, page_no)
    selected = " ".join(body.text.split())
    if not selected:
        raise HTTPException(422, "Select some text first")
    tags, level = level_of_page(user["id"], book_id, page_no)
    if body.action == "simplify":  # asking for simpler text is a sign of difficulty (design 8.3)
        add_evidence(user["id"], tags, "difficulty", book_id, page_no)
    key = (book_id, page_no, level, body.action, hashlib.sha256(selected.encode()).hexdigest())

    def cached():
        rows = q("SELECT result FROM text_notes WHERE book_id = %s AND page_no = %s AND level = %s "
                 "AND action = %s AND text_hash = %s", key)
        return {"result": rows[0]["result"]} if rows else None

    if note := cached():
        return note
    with llm_lock:
        if note := cached():
            return note
        try:
            # A formula is often a picture in the PDF, so Explain also gets the page picture.
            image = page_image(book["file_path"], page_no) if body.action == "explain" else None
            result, usage = llm.selection(body.action, selected, text, level, image)
        except llm.LLMError as e:
            raise HTTPException(502, str(e))
        q("INSERT INTO text_notes (book_id, page_no, level, action, text_hash, result) "
          "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", (*key, result))
        log_usage(user["id"], body.action, usage)
    return cached()


@app.get("/api/books/{book_id}/pages/{page_no}/check")
def page_check(book_id: int, page_no: int, user: dict = Depends(current_user)):
    """Two questions about the page. The answers are sent too: this is practice, graded in the browser."""
    book = get_book(book_id, user)
    text = page_text(book_id, page_no)

    def cached():
        rows = q("SELECT check_json FROM page_checks WHERE book_id = %s AND page_no = %s", (book_id, page_no))
        return rows[0]["check_json"] if rows else None

    if check := cached():
        return check
    with llm_lock:
        if check := cached():
            return check
        image = page_image(book["file_path"], page_no)
        if len(text.strip()) < MIN_PAGE_CHARS and image is None:
            raise HTTPException(404, "There is nothing on this page to ask about")
        try:
            result, usage = llm.page_check(text, image)
        except llm.LLMError as e:
            raise HTTPException(502, str(e))
        q("INSERT INTO page_checks (book_id, page_no, check_json) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
          (book_id, page_no, Jsonb(result.model_dump())))
        log_usage(user["id"], "page_check", usage)
    return cached()


MAX_GUIDE_PAGES = 60  # about 60,000 input tokens for a dense book. Same number as GUIDE_MAX_PAGES in App.jsx.


@app.get("/api/books/{book_id}/guide")
def chapter_guide(book_id: int, start: int, end: int, only_saved: bool = False, user: dict = Depends(current_user)):
    """Pages start..end (a chapter or a section) as one guide: the key ideas, the details that are easy
    to miss, and a line for every page. only_saved: never call the LLM."""
    book = get_book(book_id, user)
    if not 1 <= start <= end <= (book["page_count"] or 0):
        raise HTTPException(422, "These pages are not in the book")
    if end - start + 1 > MAX_GUIDE_PAGES:
        raise HTTPException(422, f"This part is longer than {MAX_GUIDE_PAGES} pages. Pick a smaller section.")

    def cached():
        rows = q("SELECT guide_json, level FROM chapter_guides WHERE book_id = %s AND start_page = %s "
                 "AND end_page = %s", (book_id, start, end))
        return {**rows[0]["guide_json"], "level": rows[0]["level"]} if rows else None

    if guide := cached():
        return guide
    if only_saved:
        return {"not_made": True}
    pages = q("SELECT page_no, text_md FROM pages WHERE book_id = %s AND page_no BETWEEN %s AND %s ORDER BY page_no",
              (book_id, start, end))
    if sum(len(p["text_md"].strip()) for p in pages) < MIN_PAGE_CHARS:
        raise HTTPException(404, "No text found on these pages. They may be pictures or scanned pages.")
    # ponytail: the level comes from the pages of the chapter that are already tagged (no tagging calls
    # here), and the guide is made once at that level. Add a level to the key if readers want it remade.
    tags = q("SELECT DISTINCT unnest(skills) AS skill FROM pages WHERE book_id = %s AND page_no BETWEEN %s AND %s",
             (book_id, start, end))
    level = profile.page_level([s["level"] for s in skill_rows(user["id"], [t["skill"] for t in tags])])
    figures = figure_pages(book["file_path"], start, end)
    with llm_lock:  # ponytail: a long call (up to minutes). Page cards wait behind it; see llm_lock.
        if guide := cached():
            return guide
        try:
            result, usage = llm.chapter_guide(book["title"], [(p["page_no"], p["text_md"]) for p in pages],
                                              figures, level)
        except llm.LLMError as e:
            raise HTTPException(502, str(e))
        q("INSERT INTO chapter_guides (book_id, start_page, end_page, level, guide_json) VALUES (%s, %s, %s, %s, %s) "
          "ON CONFLICT DO NOTHING", (book_id, start, end, level, Jsonb(llm.checked_guide(result, start, end, figures))))
        log_usage(user["id"], "chapter_guide", usage)
    return cached()


@app.get("/api/books/{book_id}/toc")
def book_toc(book_id: int, user: dict = Depends(current_user)):
    """The book's own table of contents, in order: level 1 is a chapter, 2 a section inside it, and so on.
    Empty if the PDF has none."""
    book = get_book(book_id, user)
    return [{"level": level, "title": title.strip(), "page": page}
            for level, title, page in toc(book["file_path"]) if page >= 1]


@app.get("/api/books/{book_id}/chapters")
def book_chapters(book_id: int, user: dict = Depends(current_user)):
    """The book cut into chapters, [{title, start, end}]. Notes, chats and highlights are grouped by these."""
    book = get_book(book_id, user)
    return chapters(toc(book["file_path"]), book["page_count"] or 0)


# ---- highlights and the notes of a book ------------------------------------

HIGHLIGHT_COLS = "id, page_no, color, text, rects, created_at"


class Highlight(BaseModel):
    page: int = Field(ge=1)
    color: Literal["yellow", "green", "pink", "blue"]
    text: str = Field(min_length=1, max_length=2000)
    # One box per line of text: left, top, width, height, each a part of the page size.
    rects: list[tuple[float, float, float, float]] = Field(min_length=1, max_length=200)


@app.get("/api/books/{book_id}/highlights")
def list_highlights(book_id: int, user: dict = Depends(current_user)):
    get_book(book_id, user)
    return q(f"SELECT {HIGHLIGHT_COLS} FROM highlights WHERE book_id = %s ORDER BY page_no, id", (book_id,))


@app.post("/api/books/{book_id}/highlights")
def add_highlight(book_id: int, body: Highlight, user: dict = Depends(current_user)):
    book = get_book(book_id, user)
    if body.page > (book["page_count"] or 0) or any(not 0 <= v <= 1 for rect in body.rects for v in rect):
        raise HTTPException(422, "This highlight is not on the page")
    return q(f"INSERT INTO highlights (book_id, page_no, color, text, rects) VALUES (%s, %s, %s, %s, %s) "
             f"RETURNING {HIGHLIGHT_COLS}", (book_id, body.page, body.color, body.text, Jsonb(body.rects)))[0]


@app.delete("/api/highlights/{highlight_id}")
def delete_highlight(highlight_id: int, user: dict = Depends(current_user)):
    q("DELETE FROM highlights h USING books b WHERE h.id = %s AND b.id = h.book_id AND b.user_id = %s",
      (highlight_id, user["id"]))
    return {}


# The newest saved summary of each page, whatever level it was written for.
CARDS_SQL = ("SELECT DISTINCT ON (page_no) page_no, card_json->'bullets' AS bullets FROM page_cards "
             "WHERE book_id = %s {more} ORDER BY page_no, created_at DESC")


def chat_list(book_id: int, user_id: int) -> list[dict]:
    """page_no: the page the chat started on. It decides the chapter the chat is shown under."""
    return q("SELECT c.id, c.title, c.created_at, c.start_page, c.end_page, "
             "coalesce(c.start_page, (SELECT m.page_no FROM messages m WHERE m.chat_id = c.id ORDER BY m.id LIMIT 1)) "
             "AS page_no "
             "FROM chats c WHERE c.book_id = %s AND c.user_id = %s ORDER BY c.created_at DESC", (book_id, user_id))


@app.get("/api/books/{book_id}/notes")
def book_notes(book_id: int, user: dict = Depends(current_user)):
    """Everything saved for this book that has a page: summaries, chats, chapter guides. No LLM call.
    The app groups it by chapter. Highlights come from their own endpoint."""
    get_book(book_id, user)
    return {
        "cards": q(CARDS_SQL.format(more=""), (book_id,)),
        "chats": chat_list(book_id, user["id"]),
        "guides": q("SELECT start_page, end_page, guide_json->>'summary' AS summary FROM chapter_guides "
                    "WHERE book_id = %s ORDER BY start_page, end_page DESC", (book_id,)),
    }


def reader_notes(book: dict, user_id: int, page_no: int, pages: list[int], chat_id: int | None,
                 scope: tuple[int, int] | None = None) -> dict:
    """What the reader already saved around a question, for the chat. `pages` are the pages the book
    search found. Everything here is looked up by page number: no second search, no LLM call.
    scope: the pages of a chat started from a chapter guide. Then that whole guide is given."""
    bid = book["id"]
    chapter = next(c for c in reversed(chapters(toc(book["file_path"]), book["page_count"])) if c["start"] <= page_no)
    if scope:
        guide = q("SELECT guide_json FROM chapter_guides WHERE book_id = %s AND start_page = %s AND end_page = %s",
                  (bid, *scope))
    else:
        guide = q("SELECT jsonb_build_object('summary', guide_json->'summary') AS guide_json FROM chapter_guides "
                  "WHERE book_id = %s AND start_page <= %s AND end_page >= %s ORDER BY end_page - start_page DESC "
                  "LIMIT 1", (bid, page_no, page_no))
    return {
        "chapter": chapter["title"],
        "guide": guide[0]["guide_json"] if guide else None,
        "cards": q(CARDS_SQL.format(more="AND page_no = ANY(%s)"), (bid, pages)),
        "highlights": q("SELECT page_no, text FROM highlights WHERE book_id = %s AND page_no = ANY(%s) "
                        "ORDER BY page_no, id LIMIT 12", (bid, pages)),
        # ponytail: the last 3 questions from other chats in this chapter, not the best-matching ones.
        # Search over old messages if chapters get many chats.
        "earlier": q("SELECT a.text AS question, b.text AS answer FROM messages a "
                     "JOIN chats c ON c.id = a.chat_id "
                     "JOIN messages b ON b.id = (SELECT min(id) FROM messages WHERE chat_id = a.chat_id AND id > a.id) "
                     "WHERE c.book_id = %s AND c.user_id = %s AND a.role = 'user' AND b.role = 'assistant' "
                     "AND a.page_no BETWEEN %s AND %s AND a.chat_id IS DISTINCT FROM %s ORDER BY a.id DESC LIMIT 3",
                     (bid, user_id, chapter["start"], chapter["end"], chat_id))[::-1],
    }


@app.get("/api/usage")
def usage_summary(user: dict = Depends(current_user)):
    """LLM use per feature and per day. These numbers are needed later to set a price (design 15)."""
    by_feature = q("SELECT feature, model, count(*) AS calls, sum(tokens_in)::bigint AS tokens_in, "
                   "sum(tokens_out)::bigint AS tokens_out, round(avg(ms))::int AS avg_ms FROM usage_log "
                   "WHERE user_id = %s GROUP BY 1, 2 ORDER BY tokens_in DESC", (user["id"],))
    by_day = q("SELECT created_at::date AS day, count(*) AS calls, sum(tokens_in)::bigint AS tokens_in, "
               "sum(tokens_out)::bigint AS tokens_out FROM usage_log WHERE user_id = %s "
               "AND created_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1 DESC", (user["id"],))
    pages = q("SELECT count(DISTINCT (c.book_id, c.page_no)) AS n FROM page_cards c JOIN books b ON b.id = c.book_id "
              "WHERE b.user_id = %s", (user["id"],))[0]["n"]
    return {"by_feature": by_feature, "by_day": by_day, "pages_explained": pages}


# ---- reader profile (design section 8) -------------------------------------
# The LLM only tags pages and suggests signals. Every level change is decided by reader_profile.decide.

def known_skills(user_id: int) -> list[str]:
    rows = q("SELECT skill FROM reader_skills WHERE user_id = %s UNION "
             "SELECT unnest(p.skills) FROM pages p JOIN books b ON b.id = p.book_id WHERE b.user_id = %s",
             (user_id, user_id))
    return sorted(set(profile.SEED_SKILLS) | {r["skill"] for r in rows})


def skills_of_page(book_id: int, page_no: int, text: str, user: dict) -> list[str]:
    """Skills this page needs. Tagged once by the small model, then read from the pages table."""
    def saved():
        return q("SELECT skills FROM pages WHERE book_id = %s AND page_no = %s", (book_id, page_no))[0]["skills"]

    if (tags := saved()) is not None:
        return tags
    if not text.strip():
        return []
    with llm_lock:
        if (tags := saved()) is not None:
            return tags
        try:
            tags, usage = llm.page_skills(text, known_skills(user["id"]))
        except llm.LLMError:
            return []  # the card is still made, at the default level. Tagging is tried again next time.
        tags = [t for t in dict.fromkeys(tags) if profile.valid_skill(t)]
        q("UPDATE pages SET skills = %s WHERE book_id = %s AND page_no = %s", (tags, book_id, page_no))
        log_usage(user["id"], "page_skills", usage)
    return tags


def skill_rows(user_id: int, skills: list[str] | None = None) -> list[dict]:
    """Level, source and the open question (if any) for each skill. skills=None: every skill with data."""
    saved = {r["skill"]: r for r in q(
        "SELECT skill, level, source, updated_at, extract(epoch FROM now() - updated_at) / 86400 AS age_days "
        "FROM reader_skills WHERE user_id = %s", (user_id,))}
    if skills is None:
        seen = q("SELECT DISTINCT skill FROM skill_evidence WHERE user_id = %s", (user_id,))
        skills = sorted(set(saved) | {r["skill"] for r in seen})
    out = []
    for skill in skills:
        row = saved.get(skill, {"level": profile.DEFAULT_LEVEL, "source": "inferred", "updated_at": None, "age_days": 0})
        # Only evidence from after the last change counts, so one change does not trigger the next.
        signals = [r["signal"] for r in q(
            "SELECT signal FROM skill_evidence WHERE user_id = %s AND skill = %s "
            "AND created_at > coalesce(%s, '-infinity'::timestamptz) ORDER BY id DESC LIMIT 20",
            (user_id, skill, row["updated_at"]))]
        decision = profile.decide(row["level"], row["source"], float(row["age_days"]), signals)
        out.append({"skill": skill, "level": row["level"], "source": row["source"], "updated_at": row["updated_at"],
                    "suggest": {"kind": decision[0], "level": decision[1]} if decision else None})
    return out


def set_level(user_id: int, skill: str, level: str, source: str) -> None:
    old = skill_rows(user_id, [skill])[0]
    with pool.connection() as conn, conn.transaction():
        conn.execute("INSERT INTO reader_skills (user_id, skill, level, source) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT (user_id, skill) DO UPDATE SET level = EXCLUDED.level, source = EXCLUDED.source, "
                     "updated_at = now()", (user_id, skill, level, source))
        conn.execute("INSERT INTO skill_history (user_id, skill, old_level, new_level, source) "
                     "VALUES (%s, %s, %s, %s, %s)", (user_id, skill, old["level"], level, source))


def add_evidence(user_id: int, skills: list[str], signal: str, book_id: int | None, page_no: int | None) -> None:
    for skill in skills:
        q("INSERT INTO skill_evidence (user_id, skill, signal, book_id, page_no) VALUES (%s, %s, %s, %s, %s) "
          "ON CONFLICT DO NOTHING", (user_id, skill, signal, book_id, page_no))
        suggest = skill_rows(user_id, [skill])[0]["suggest"]
        if suggest and suggest["kind"] == "lower":  # going simpler needs no question
            set_level(user_id, skill, suggest["level"], "inferred")


class PageSignal(BaseModel):
    # difficulty: clicked Simpler. comfort: clicked Too simple. read: stayed on the page and moved on.
    signal: Literal["difficulty", "comfort", "read"]


@app.post("/api/books/{book_id}/pages/{page_no}/signal")
def page_signal(book_id: int, page_no: int, body: PageSignal, user: dict = Depends(current_user)):
    get_book(book_id, user)
    rows = q("SELECT skills FROM pages WHERE book_id = %s AND page_no = %s", (book_id, page_no))
    skills = (rows[0]["skills"] if rows else None) or []
    signal = body.signal
    if signal == "read":
        # A page read with no request for help is a sign of comfort. A page where help was needed is not.
        if q("SELECT 1 FROM skill_evidence WHERE user_id = %s AND book_id = %s AND page_no = %s "
             "AND signal = 'difficulty' LIMIT 1", (user["id"], book_id, page_no)):
            return {}
        signal = "comfort"
    add_evidence(user["id"], skills, signal, book_id, page_no)
    return {}


@app.get("/api/skills")
def my_skills(user: dict = Depends(current_user)):
    """Everything the tool tracks about the reader, with the evidence behind it."""
    counts = {(r["skill"], r["signal"]): r["n"] for r in q(
        "SELECT skill, signal, count(*) AS n FROM skill_evidence WHERE user_id = %s GROUP BY 1, 2", (user["id"],))}
    recent = q("SELECT e.skill, e.signal, e.page_no, e.created_at, b.title FROM skill_evidence e "
               "LEFT JOIN books b ON b.id = e.book_id WHERE e.user_id = %s ORDER BY e.id DESC LIMIT 300", (user["id"],))
    rows = skill_rows(user["id"])
    for row in rows:
        row["difficulty"] = counts.get((row["skill"], "difficulty"), 0)
        row["comfort"] = counts.get((row["skill"], "comfort"), 0)
        row["recent"] = [e for e in recent if e["skill"] == row["skill"]][:5]
    return rows


class SetLevel(BaseModel):
    level: Literal["beginner", "learning", "comfortable"]


@app.put("/api/skills/{skill}")
def state_level(skill: str, body: SetLevel, user: dict = Depends(current_user)):
    """The reader sets a level. Stated beats inferred."""
    if not profile.valid_skill(skill):
        raise HTTPException(422, "Not a skill name")
    set_level(user["id"], skill, body.level, "stated")
    return {}


@app.post("/api/skills/{skill}/decline")
def decline_suggestion(skill: str, user: dict = Depends(current_user)):
    """The reader answered "not yet". Saved as evidence, so the same question does not come back at once."""
    if not profile.valid_skill(skill):
        raise HTTPException(422, "Not a skill name")
    q("INSERT INTO skill_evidence (user_id, skill, signal) VALUES (%s, %s, 'declined')", (user["id"], skill))
    return {}


# ---- chat ------------------------------------------------------------------

HISTORY_MESSAGES = 6  # ponytail: only the last few messages are sent. Add a summary of older ones for long chats.
MESSAGE_COLS = "id, role, text, page_no, source_pages, created_at"


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    page: int
    whole_book: bool = False     # False: search only pages up to the current one
    chat_id: int | None = None   # None: start a new chat
    # A new chat from a chapter guide: search only these pages. Saved on the chat for later questions.
    start: int | None = None
    end: int | None = None


@app.get("/api/books/{book_id}/chats")
def list_chats(book_id: int, user: dict = Depends(current_user)):
    return chat_list(book_id, user["id"])


@app.get("/api/chats/{chat_id}/messages")
def list_messages(chat_id: int, user: dict = Depends(current_user)):
    if not q("SELECT 1 FROM chats WHERE id = %s AND user_id = %s", (chat_id, user["id"])):
        raise HTTPException(404, "Chat not found")
    return q(f"SELECT {MESSAGE_COLS} FROM messages WHERE chat_id = %s ORDER BY id", (chat_id,))


@app.post("/api/books/{book_id}/ask")
def ask(book_id: int, body: Ask, user: dict = Depends(current_user)):
    book = get_book(book_id, user)
    if book["index_status"] != "ready":
        raise HTTPException(409, "Search is still being prepared for this book. Try again in a minute.")
    question = body.text.strip()
    history = []
    scope = None
    if body.chat_id is not None:
        rows = q("SELECT start_page, end_page FROM chats WHERE id = %s AND user_id = %s AND book_id = %s",
                 (body.chat_id, user["id"], book_id))
        if not rows:
            raise HTTPException(404, "Chat not found")
        if rows[0]["start_page"]:
            scope = (rows[0]["start_page"], rows[0]["end_page"])
        history = q("SELECT role, text FROM messages WHERE chat_id = %s ORDER BY id DESC LIMIT %s",
                    (body.chat_id, HISTORY_MESSAGES))[::-1]
    elif body.start is not None and body.end is not None:
        if not 1 <= body.start <= body.end <= (book["page_count"] or 0):
            raise HTTPException(422, "These pages are not in the book")
        scope = (body.start, body.end)

    # Free work first: search and rerank run locally.
    text = page_text(book_id, body.page)
    if scope:  # a chapter chat: the whole chapter, even the pages after the current one
        hits = search.search(question, user["id"], book_id, min_page=scope[0], max_page=scope[1])
    else:
        hits = search.search(question, user["id"], book_id, max_page=None if body.whole_book else body.page)
    _, level = level_of_page(user["id"], book_id, body.page)
    skills = known_skills(user["id"])
    # The search found the pages. What the reader saved on those pages is read straight from the database.
    notes = reader_notes(book, user["id"], body.page, sorted({h["page_no"] for h in hits} | {body.page}), body.chat_id,
                         scope)
    with llm_lock:
        try:
            result, usage = llm.answer(question, body.page, text, hits, history, level, skills,
                                       page_image(book["file_path"], body.page), notes)
        except llm.LLMError as e:
            raise HTTPException(502, str(e))  # nothing is saved; the reader can ask again

    # The LLM only suggests a signal. The app checks the skill name and applies the rules.
    note, sig = None, result.skill_signal
    if sig and sig.skill in skills:
        if sig.signal == "difficulty":
            add_evidence(user["id"], [sig.skill], "difficulty", book_id, body.page)
        else:
            stated = sig.signal.removeprefix("stated_")
            set_level(user["id"], sig.skill, stated, "stated")
            note = f"Saved in My skills: {sig.skill.replace('_', ' ')} is now “{stated}”."

    # The LLM only suggests the sources. Keep the pages that were really in its context.
    given = {h["page_no"] for h in hits} | {body.page}
    sources = sorted({p for p in result.source_pages if p in given})
    with pool.connection() as conn, conn.transaction():
        chat_id = body.chat_id or conn.execute(
            "INSERT INTO chats (user_id, book_id, title, start_page, end_page) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user["id"], book_id, question[:60], *(scope or (None, None)))).fetchone()["id"]
        insert = (f"INSERT INTO messages (chat_id, role, text, page_no, source_pages) "
                  f"VALUES (%s, %s, %s, %s, %s) RETURNING {MESSAGE_COLS}")
        asked = conn.execute(insert, (chat_id, "user", question, body.page, [])).fetchone()
        answered = conn.execute(insert, (chat_id, "assistant", result.answer, body.page, sources)).fetchone()
    log_usage(user["id"], "chat", usage)
    return {"chat_id": chat_id, "messages": [asked, answered], "profile_note": note}
