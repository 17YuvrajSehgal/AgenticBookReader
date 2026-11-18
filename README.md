# Reading Companion

Design: [reading-companion-design.md](reading-companion-design.md). All 5 phases are built.

## Run

Needs Docker Desktop running and the `claude` CLI logged in.

```powershell
docker compose up -d --wait                              # Postgres on 127.0.0.1:5433, Qdrant on 6333
uv pip install --python .venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt

cd backend;  ..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
cd frontend; npm install; npm run dev                    # open http://127.0.0.1:5173
```

With no settings, the login has no check. Keep it on 127.0.0.1 in that case.

## Running it for other people

Set these before starting the backend. Each one is off when it is not set.

| What | Settings |
|---|---|
| Login by email link | `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, and `APP_URL` (the address readers open, default `http://127.0.0.1:5173`) |
| Claude API in place of Claude Code | `LLM_PROVIDER=api`, `ANTHROPIC_API_KEY`. `LLM_EFFORT` sets how much the model thinks (default `low`) |
| Files in object storage | `S3_BUCKET`, `S3_ENDPOINT_URL` for a store that is not AWS, and the usual `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION`. The local data folder is then only a cache |

Still to do before a real launch: serve over HTTPS, serve the built frontend (`npm run build`) from a web server,
and make the one-call-at-a-time LLM lock per account.

## Settings (environment variables)

- `LLM_MODEL`: model for page cards and chat. Default `sonnet`.
- `LLM_SMALL_MODEL`: model for small jobs (tagging the skills of a page). Default `haiku`.
- `DATABASE_URL`, `DATA_DIR`: where Postgres and the book files are.

- `PREMAKE_PAGES`: how many page cards are made ahead of the reader. Default `2`. `0` turns it off and saves LLM usage.
- `QDRANT_URL`: the search store. Default `http://127.0.0.1:6333`.

The first start downloads BGE-M3 and the reranker (about 4.5 GB). Together they use about 2.2 GB of VRAM.

## Checks

```powershell
cd backend
..\.venv\Scripts\python.exe llm.py                          # one real LLM call
..\.venv\Scripts\python.exe search.py                       # chunking, no models needed
..\.venv\Scripts\python.exe reader_profile.py               # the rules that change a reader's level
..\.venv\Scripts\python.exe eval_retrieval.py make --book 5 # write 50 test questions (LLM, once)
..\.venv\Scripts\python.exe eval_retrieval.py run --book 5  # Recall@5 and MRR for the 4 search modes
```

Results are saved in `eval/`.
