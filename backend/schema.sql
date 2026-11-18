-- Tables are added in the phase that uses them. This file runs on every start, so every
-- statement must be safe to run again.
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    file_path   TEXT NOT NULL DEFAULT '',
    file_hash   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'parsing',   -- parsing | ready | error
    error       TEXT,                              -- ponytail: lives here until a jobs table is needed
    page_count  INT,
    last_page   INT NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, file_hash)
);

CREATE TABLE IF NOT EXISTS pages (
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    text_md     TEXT NOT NULL,
    PRIMARY KEY (book_id, page_no)
);

CREATE TABLE IF NOT EXISTS page_cards (
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    level       TEXT NOT NULL,
    card_json   JSONB NOT NULL,
    model       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book_id, page_no, level)
);

-- Phase 2 ---------------------------------------------------------------

-- pending | indexing | ready | error. The book can be read while this is not ready; only chat waits.
ALTER TABLE books ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'pending';

-- The hard-words list on page cards was removed, and with it the concept view that was built from it.
DROP VIEW IF EXISTS concepts;

CREATE TABLE IF NOT EXISTS chats (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id       BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role          TEXT NOT NULL,              -- user | assistant
    text          TEXT NOT NULL,
    page_no       INT NOT NULL,               -- the page the reader was on
    source_pages  INT[] NOT NULL DEFAULT '{}',-- pages the answer used
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 3 ---------------------------------------------------------------

-- Skills a page needs, tagged once by the small model. NULL = not tagged yet.
ALTER TABLE pages ADD COLUMN IF NOT EXISTS skills TEXT[];

-- The profile belongs to the user, not to a book. No row for a skill = the default level.
CREATE TABLE IF NOT EXISTS reader_skills (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    level       TEXT NOT NULL,     -- beginner | learning | comfortable
    source      TEXT NOT NULL,     -- inferred (the tool guessed) | stated (the reader said it)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, skill)
);

-- Every change, never overwritten.
CREATE TABLE IF NOT EXISTS skill_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    old_level   TEXT NOT NULL,
    new_level   TEXT NOT NULL,
    source      TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS skill_evidence (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    signal      TEXT NOT NULL,     -- difficulty | comfort | declined (the reader said "not yet")
    book_id     BIGINT REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One page gives one signal of each kind per skill, however often a button is clicked.
CREATE UNIQUE INDEX IF NOT EXISTS skill_evidence_once
    ON skill_evidence (user_id, skill, signal, book_id, page_no);

-- Phase 4 ---------------------------------------------------------------

-- Saved results of Simplify / Explain / Define on selected text. The same selection is never sent twice.
CREATE TABLE IF NOT EXISTS text_notes (
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    level       TEXT NOT NULL,
    action      TEXT NOT NULL,     -- simplify | explain | define
    text_hash   TEXT NOT NULL,     -- sha256 of the selected text
    result      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book_id, page_no, level, action, text_hash)
);

-- The 2-question check of a page. Made once, when the reader first asks for it.
CREATE TABLE IF NOT EXISTS page_checks (
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    check_json  JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book_id, page_no)
);

-- Phase 5 ---------------------------------------------------------------

-- Magic links. Only a hash of the token is saved, and a link works once.
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

-- Files are now named by a storage key, not by a path on one machine.
UPDATE books SET file_path = 'users/' || user_id || '/books/' || id || '/original.pdf'
WHERE file_path <> '' AND file_path NOT LIKE 'users/%';

-- Deleting a book must not delete what the tool learned about the reader from it.
ALTER TABLE skill_evidence DROP CONSTRAINT IF EXISTS skill_evidence_book_id_fkey;
ALTER TABLE skill_evidence ADD CONSTRAINT skill_evidence_book_id_fkey
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE SET NULL;

-- Chapter guide ---------------------------------------------------------

-- A whole chapter (or section) in one guide. One per page range, made when the reader asks.
CREATE TABLE IF NOT EXISTS chapter_guides (
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    start_page  INT NOT NULL,
    end_page    INT NOT NULL,
    level       TEXT NOT NULL,     -- the reader's level when it was made
    guide_json  JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book_id, start_page, end_page)
);

-- Highlights ------------------------------------------------------------

-- Text the reader marked on a page. rects: [[left, top, width, height], ...] as parts of the page
-- size (0 to 1), so they fit at any zoom.
CREATE TABLE IF NOT EXISTS highlights (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id     BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    color       TEXT NOT NULL,     -- yellow | green | pink | blue
    text        TEXT NOT NULL,
    rects       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS highlights_book ON highlights (book_id, page_no);

CREATE TABLE IF NOT EXISTS usage_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature     TEXT NOT NULL,
    model       TEXT NOT NULL,
    tokens_in   INT NOT NULL,
    tokens_out  INT NOT NULL,
    ms          INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
