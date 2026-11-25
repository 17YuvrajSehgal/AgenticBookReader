# Reading Companion: Design Document

Status: draft, version 0.1 (November 17, 2026)

## 1. What we are building

A reading tool that sits beside a book, like a scratchpad. The book is on the left. A helper panel is on the right. The helper reads the current page and explains it in simple words. It also remembers the whole book and remembers the reader.

It is for people who:

- get distracted easily while reading
- find hard words and long sentences difficult
- read in a language that is not their first language
- want to learn faster without reading every line

The short description: a smarter and cheaper Claude chat, made only for reading books.

## 2. Main rules of the design

These rules decide everything else in this document.

1. **Process the book once, save everything.** No page is ever sent to the LLM twice for the same job.
2. **The app owns all state.** The LLM only suggests changes. Normal code decides what is saved. This keeps memory, chats, and notes in sync.
3. **Free work first, LLM work last.** Parsing, search, and reranking run on the local machine. The LLM is used only for explaining and for short notes.
4. **Lazy work.** LLM notes for a chapter are made when the reader reaches that chapter, not before.
5. **Every answer points to a page.** The reader can always check the source.
6. **Built for many users from day one.** Every table has a `user_id`, even while there is one user.
7. **Every outside part sits behind a small interface.** The LLM, the parser, the embedder, and the search store can each be replaced without touching the rest.

## 3. What the reader sees

### 3.1 Screen layout

- Left: the book page (PDF or EPUB).
- Right: the helper panel with three tabs: **Page**, **Chat**, **My notes**.

### 3.2 Page tab (the "before you read" card)

Shown as soon as a page opens. It is loaded from disk if it already exists.

- The page in 3 bullets
- Hard words on this page, each with a simple meaning
- Formulas on this page, each explained in words
- "From earlier": things this page uses that were taught before, with page links

The reader can then decide: read the original page, or move on.

### 3.3 Actions on the book text

- Select a sentence and click **Simplify**: get the same sentence in easy words.
- Select a formula and click **Explain**: get each symbol explained, then a small example with numbers.
- Select a word and click **Define**: get the meaning as used in this book.

### 3.4 Chat tab

- Free questions about the page or the book.
- Each book has many chats, like threads in Claude chat.
- Each answer shows page links for where the facts came from.

### 3.5 Focus helpers

- One idea at a time: the card shows short blocks, not a wall of text.
- Read aloud (browser text to speech; no LLM cost).
- A 2-question check at the end of a page or section. Optional.
- Progress bar per chapter.

### 3.6 Settings

- **My skills** page: every skill the tool tracks, the current level, and why. The reader can change any of them.
- Export my data. Delete my data.

## 4. System overview

```
                 +---------------------------+
                 |   Browser (React)         |
                 |   PDF.js view + helper    |
                 +-------------+-------------+
                               |
                 +-------------v-------------+
                 |   Backend (FastAPI)       |
                 |   auth, books, chats,     |
                 |   context builder,        |
                 |   profile rules           |
                 +---+-------+--------+------+
                     |       |        |
        +------------v+  +---v----+  +v----------------+
        | Postgres    |  | Qdrant |  | LLM queue       |
        | users, books|  | search |  | one call at a   |
        | chats, notes|  | index  |  | time per account|
        | profile     |  +--------+  +--------+--------+
        +-------------+                       |
                                     +--------v--------+
        +----------------------+     | LLM interface   |
        | Ingest worker        |     | now: Claude Code|
        | parse, chunk, embed  |     | later: API      |
        | (local GPU, no LLM)  |     +-----------------+
        +----------------------+
```

Two paths matter:

- **Ingest path** (once per book): upload, parse, chunk, embed, index. No LLM.
- **Reading path** (every page and question): search locally, build a small prompt, call the LLM once, save the result.

## 5. Book ingestion

### 5.1 Steps

| Step | What happens | Uses LLM? |
|---|---|---|
| 1. Upload | Save the file. Compute a hash of it. | No |
| 2. Parse | Turn the book into markdown with headings, page numbers, and LaTeX for formulas. | No |
| 3. Build the tree | Make the chapter and section tree from the headings or the book's table of contents. | No |
| 4. Chunk | Split by section. Target 300 to 500 tokens per chunk. Never split inside a formula or an example. | No |
| 5. Add context | Put the heading path at the start of each chunk, for example `Chapter 3 > Limits > Example 2`. | No |
| 6. Embed | Make dense and sparse vectors for each chunk. | No |
| 7. Index | Store vectors in Qdrant with `user_id`, `book_id`, `page`, `section_id`. | No |
| 8. Notes (lazy) | Page cards, section summaries, concept list. Made when the reader reaches the chapter. | Yes |

After step 7 the whole book is searchable. The reader can start right away.

### 5.2 Parsing

- EPUB: use the text directly. No OCR needed. This is the best input when it exists.
- PDF with a text layer: fast path.
- Scanned PDF or heavy math: needs a layout and OCR model.

Two parser candidates, both behind one `Parser` interface:

- **Marker**: fast, good on math, runs on a laptop GPU. The weights have a licence limit for companies above 5 million dollars in revenue or funding.
- **MinerU**: strong on formulas and tables. Slower. Custom licence, must be read before a commercial launch.

The public benchmarks disagree about which is better on math, and some numbers are published by the tool makers themselves. So the choice is made by a test: run both on 20 hard pages from a real book and compare by eye.

Docling is the safe licence choice (MIT), but it is weaker on complex formulas.

### 5.3 Size and time (estimates)

- A 3,000-page book is about 1.5 to 2 million tokens.
- That is about 6,000 to 10,000 chunks.
- Dense vectors at 1024 dimensions: about 40 MB per book.
- Embedding on the laptop GPU: minutes, not hours.
- Parsing is the slow step for scanned books. It runs as a background job with progress saved per page, so a crash does not lose work.

### 5.4 GPU memory on the laptop (8 GB VRAM)

- BGE-M3 in half precision: about 1 to 3 GB with batches.
- Reranker (0.6B): about 1.5 to 2 GB.
- Parser models: about 3 GB.
- Do not run the parser and the embedder at the same time. Run parse first, free the GPU, then embed.
- Start with batch size 16 for embedding. Lower it if memory is tight.

## 6. Retrieval (how the tool finds the right part of the book)

### 6.1 Why not one method

- Keyword search alone misses meaning. "How fast it changes" does not match "derivative".
- Vector search alone misses exact terms, formula names, and numbers.
- Benchmarks in 2026 agree: combine both, then rerank. In one benchmark, hybrid plus a neural reranker reached Recall@5 of 0.816 and beat every single method.
- Anthropic's contextual retrieval work reports 35% fewer retrieval failures with context added to embeddings, 49% with keyword search added, and 67% with a reranker added.

### 6.2 Four ways to find things

The app picks the way by the type of request. No LLM is needed to choose.

1. **Concept lookup.** The reader clicks a term or asks "what is X". The app looks in the concept table. This is a normal database query. It is the fastest and most exact.
2. **Hybrid search.** For free questions. Dense vectors plus learned sparse vectors from BGE-M3, joined with Reciprocal Rank Fusion. Take the top 30.
3. **Rerank.** A small reranker model scores the 30 chunks against the question. Keep the top 5.
4. **Tree walk.** For broad questions like "what was chapter 4 about". The app uses the section tree and the saved summaries. This idea comes from PageIndex, which uses a table-of-contents tree in place of vectors. We use the tree only for broad questions, because walking it with the LLM on every question would use too much of the usage limit.

### 6.3 Filters

- Always filter by `user_id` and `book_id`.
- **Pages already read** filter: for "I forgot X" questions, search only pages up to the current one. This stops answers from using material the reader has not seen.
- The reader can turn this filter off ("search the whole book").

### 6.4 Contextual chunks, the cheap way

Anthropic's method asks an LLM to write a context line for every chunk. For 10,000 chunks that costs too much on a subscription. So:

- Version 1: the heading path is the context. It costs nothing.
- Later: add LLM-written context lazily, only for chapters the reader reaches, and only if the evaluation shows it helps.

## 7. What goes into each LLM call

The context builder is normal code. It fills a fixed budget.

| Part | Rough size |
|---|---|
| Instructions and output format | 300 tokens |
| Reader profile lines for skills on this page | 150 tokens |
| Current page text | 600 to 900 tokens |
| Section summary so far | 300 tokens |
| Top 5 chunks from search (for questions) | 1,500 tokens |
| Concept notes for terms on this page | 300 tokens |
| Last few chat messages | 800 tokens |
| **Total** | **about 4,000 to 5,000 tokens** |

This size stays the same for a 200-page book and a 3,000-page book.

Each call is fresh. The app does not depend on any memory inside Claude Code. The chat history lives in Postgres and the app sends the part that is needed.

## 8. Reader profile (the part that evolves)

### 8.1 What is stored

One row per skill, per user. Skills are things like `calculus_notation`, `greek_letters_in_math`, `probability_basics`, `academic_english_vocabulary`.

```json
{
  "skill": "calculus_notation",
  "level": "beginner",
  "source": "inferred",
  "updated": "2026-11-17"
}
```

- `level`: `beginner`, `learning`, or `comfortable`.
- `source`: `inferred` (the tool guessed) or `stated` (the reader said it).
- Every change is added to a history table. Old values are never overwritten. This idea is taken from memory systems that keep facts with a start and end time.

### 8.2 How the level changes how the tool explains

| Level | What the reader gets |
|---|---|
| beginner | Plain words first, each symbol named, a small example with numbers, then the formal version |
| learning | Short plain summary, then the formal version |
| comfortable | The normal explanation, no extra simplifying |

The reader can always click **Simplify** or **More detail** on any answer, at any level.

### 8.3 Signals (evidence)

Each signal is one row in `skill_evidence` with the page and the date.

Signs of difficulty:

- clicked Simplify on text that uses the skill
- asked a follow-up question about the same concept
- wrong answer in a page check

Signs of comfort:

- several pages using the skill with no Simplify click and no follow-up
- right answers in page checks
- clicked "too simple" on an answer

After each answer, one small LLM call may return a suggestion such as `{"skill": "calculus_notation", "signal": "difficulty"}`. The app saves it as evidence. The LLM never changes the level.

### 8.4 Rules for changing the level

These are code, not prompts.

- **Go down** (to simpler): 3 difficulty signals for one skill within the last 10 times the skill appeared.
- **Go up** (to less simple): 5 comfort signals in a row. The tool does not switch silently. It asks: "You seem comfortable with calculus notation now. Stop the simple explanations?"
- **Stated beats inferred.** If the reader says "I am comfortable with calculus now", the level is set to `comfortable` with `source: stated`, right away.
- **A stated level is protected.** Inferred evidence cannot change it for 14 days. After that, if difficulty signals pile up, the tool asks before lowering it.
- **Everything is visible.** The My skills page shows the level, the source, and the evidence behind it.

### 8.5 Across books

The profile belongs to the user, not to the book. A new book starts with what the tool already knows about the reader.

## 9. Chats

- User has many books. Book has many chats. Chat has many messages.
- Each message stores the page it was asked on and the pages used in the answer.
- Long chats: older messages are compressed into a short summary by one small call. The full messages stay in the database.
- Notes and highlights the reader saves are also searchable later.

## 10. Users and login

- Now: passwordless. The reader types an email. The app creates or loads the user and sets a session cookie. There is no check.
- Later: the same screen sends a magic link, or uses Google login. The tables do not change.
- Because there is no check now, the app must run only on localhost until real login is added.
- Every query filters by `user_id`. Files are stored per user. One user can never read another user's book, notes, or chats.

## 11. LLM access

One small interface. Nothing else in the app knows which provider is behind it.

```python
class LLM:
    def explain_page(self, context) -> PageCard: ...
    def answer(self, context, question) -> Answer: ...
    def simplify(self, context, text) -> str: ...
    def suggest_signal(self, exchange) -> SkillSignal | None: ...
    def summarize(self, text) -> str: ...
```

- **Now:** Claude Code in headless mode (`claude -p`), run inside the book's folder with read-only tools.
- **Later:** the Claude API, for a product with other users. A personal subscription is for personal use.
- All calls go through one queue. This protects the usage limit and gives one place for retries and timeouts.
- Every call has: a timeout, up to 2 retries with a wait between them, and a strict output format (JSON) that the app checks. A bad output is retried once, then shown as an error. It is never saved.
- Small jobs (signals, summaries) use a smaller model. Explaining and answering use the stronger model.
- Not yet known: how subscription limits apply to automated headless use. Check this before heavy use.

## 12. Data model

All tables have `user_id` where it makes sense.

| Table | Main columns |
|---|---|
| `users` | id, email, name, created_at |
| `sessions` | id, user_id, token, expires_at |
| `books` | id, user_id, title, file_path, file_hash, status, last_page, created_at |
| `sections` | id, book_id, parent_id, title, start_page, end_page, summary |
| `pages` | id, book_id, page_no, text_md, parsed_at |
| `chunks` | id, book_id, section_id, page_no, heading_path, text |
| `page_cards` | id, book_id, page_no, level, card_json, model, created_at |
| `concepts` | id, book_id, name, simple_meaning, formula_latex, first_page |
| `concept_links` | concept_id, depends_on_concept_id |
| `concept_mentions` | concept_id, page_no |
| `chats` | id, user_id, book_id, title, created_at |
| `messages` | id, chat_id, role, text, page_no, source_pages, created_at |
| `reader_skills` | id, user_id, skill, level, source, updated_at |
| `skill_history` | id, user_id, skill, old_level, new_level, source, changed_at |
| `skill_evidence` | id, user_id, skill, signal, book_id, page_no, created_at |
| `notes` | id, user_id, book_id, page_no, text, created_at |
| `usage_log` | id, user_id, feature, model, tokens_in, tokens_out, ms, created_at |
| `jobs` | id, book_id, kind, status, progress, error, updated_at |

Note on `page_cards`: the key includes `level`. A beginner card and a comfortable card for the same page are different rows. Each is made once.

## 13. Files on disk

```
data/
  users/{user_id}/
    books/{book_id}/
      original.pdf
      pages/0001.md, 0002.md, ...
      tree.json
      notes/            (cached LLM outputs, also in Postgres)
```

In a commercial version this folder moves to object storage. The paths stay the same.

## 14. Stack

| Layer | Choice | Reason |
|---|---|---|
| Frontend | React, PDF.js | Page view with text selection |
| Backend | FastAPI (Python) | Same language as the ML parts |
| App database | Postgres | Avoids a move from SQLite later |
| Search store | Qdrant | Dense and sparse vectors, hybrid search, and per-user filters are built in |
| Embedding | BGE-M3 | One model gives dense and sparse vectors. MIT licence |
| Reranker | Qwen3-Reranker-0.6B | Small enough for 8 GB VRAM |
| Parser | Marker or MinerU, behind an interface | Both give LaTeX for formulas |
| LLM | Claude Code headless now, API later | Uses the current subscription |
| Jobs | A small queue with progress saved in Postgres | Ingest can resume after a crash |
| Setup | Docker Compose, pinned versions | Same setup on any machine |

Things to keep swappable:

- Embedder: Qwen3-Embedding-0.6B scores higher than BGE-M3 on the MTEB multilingual benchmark (64.33 against 59.56) and is about 1 GB. If the evaluation shows it is better on real books, use it for dense vectors and keep BGE-M3 for sparse.
- Search store: pgvector is fine under about 10 million vectors and removes one service. Qdrant is picked because hybrid search is the core of this tool.

## 15. Keeping LLM cost low

- Cache everything by `(book, page, level, job)`. Check the cache before every call.
- Lazy notes: only chapters the reader opens.
- Pre-make the card for the next 2 pages while the reader is on the current one, so it feels instant. Do not go further ahead than that.
- Small model for small jobs.
- Fixed context budget (section 7).
- Log tokens per feature from day one. These numbers are needed later to set a price.

Rough total: a 400-page book is about 200,000 to 300,000 tokens of text. With lazy work this is paid once, over the weeks it takes to read the book.

## 16. How we check that it works

Retrieval:

- Write 50 test questions for one book, each with the page that holds the answer.
- Measure Recall@5 and MRR for: dense only, sparse only, hybrid, hybrid plus rerank.
- Repeat when changing the embedder, the chunk size, or the parser.

Explanations:

- Reading level of the output (for example a readability score) per skill level.
- Does every claim in the answer appear on the cited pages? Check a sample by hand.

Reader profile:

- How often does the reader click Simplify after a card? This should go down over time.
- How often does the reader correct a level in My skills? This should be rare.

Speed and cost:

- Time to show a cached card (target: under 200 ms).
- Time for a new answer.
- Tokens per page read.

## 17. Risks and things to settle before a commercial launch

- **Copyright.** Users upload their own copies. Book text and notes made from it are never shared between users. Get legal advice before launch.
- **Shared processing.** Two users may upload the same book. Reusing one user's parsed text for another saves cost but is a legal question. Keep it off until advised.
- **Privacy.** The profile shows what a person is weak at. Offer export and delete. Do not use it for anything except explanations.
- **Wrong explanations.** The LLM can be wrong. Page links on every answer are the main protection. Add a "report wrong answer" button.
- **Parser licences.** Marker and MinerU both have conditions. Read them before charging money.
- **LLM access.** A product for other users needs the API, not a personal subscription.
- **Parsing errors in math.** A wrong symbol makes a wrong explanation. For formulas, send the page image to the LLM as well when the parser's confidence is low.

## 18. Build order

**Phase 1: read one book**

- Docker Compose with Postgres and Qdrant
- Email login without a check, sessions
- Upload, parse, page view
- Page card for the current page, cached

**Phase 2: remember the book**

- Chunk, embed, index
- Hybrid search plus rerank
- Chat with page links
- Concept table and "From earlier" links
- The 50-question retrieval test

**Phase 3: remember the reader**

- Skill tables, evidence, rules
- Explanations change by level
- My skills page
- "I am comfortable with X" handled as a stated change

**Phase 4: polish**

- Simplify, Explain, Define on selected text
- Read aloud, page checks, progress
- Pre-make next pages
- Usage dashboard

**Phase 5: get ready for other users**

- Real login (magic link or Google)
- API provider behind the LLM interface
- Object storage
- Export and delete

## 19. Open questions

- Which books first: math-heavy textbooks or mostly text? This decides how much time goes into the parser.
- EPUB support in phase 1, or PDF only?
- How should the skill list start: a fixed list, or skills found from each book's concepts?
- Should the page check questions count as evidence, or stay only as practice?

## 20. Sources checked

- Benchmark of retrieval methods, hybrid and reranking results: https://arxiv.org/html/2604.01733v1
- Retrieval methods overview and Anthropic contextual retrieval numbers: https://blog.codercops.com/blog/rag-2026-vector-search-sparse-attention-vectorless
- PageIndex, tree index without vectors: https://github.com/VectifyAI/PageIndex
- Embedding models compared: https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag
- BGE-M3 and Qwen3-Embedding overview: https://www.premai.io/blog/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/
- Qwen3 embedding and reranker models: https://github.com/QwenLM/Qwen3-Embedding
- PDF parsers compared, with licences: https://devtoollab.com/blog/best-pdf-parsers-for-rag
- Marker benchmarks (published by its maker): https://pypi.org/project/marker-pdf/
- Docling, Marker, MinerU comparison: https://adityamangal98.medium.com/docling-vs-marker-vs-mineru-the-ultimate-open-source-pdf-parser-benchmark-2026-which-is-best-a36ecbb6c6b1
- Vector databases compared: https://alexcloudstar.com/md/blog/vector-database-comparison-2026/
- Agent memory frameworks compared: https://coworker.ai/blog/mem0-vs-zep-vs-letta
