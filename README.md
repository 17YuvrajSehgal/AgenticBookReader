# Reading Companion

A reading tool that sits beside a PDF book, explains each page in simple words with Claude, and remembers the book and the reader.
Design: [reading-companion-design.md](reading-companion-design.md).

## Run on your own computer

Needs Docker Desktop running. For Claude Code mode, the `claude` CLI signed in (`claude auth login`).

```powershell
docker compose up -d --wait                              # Postgres on 127.0.0.1:5433, Qdrant on 6333
uv pip install --python .venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt

cd backend;  $env:OWNER_EMAIL = "you@example.com"; ..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
cd frontend; npm install; npm run dev                    # open http://127.0.0.1:5173
```

Sign in with an email link. With no mail server set up, the link is printed in the backend window.
Then set a password in Settings if you like.

## Accounts and Claude

- Sign in with a password, an email link, or Google. New accounts confirm their email first.
- The **owner** is the account with `OWNER_EMAIL` (or the first account on a new install). The owner sees an admin page:
  every account, turn accounts off, remove them, make admins.
- Each reader connects Claude in **Settings**:
  - **Their own Anthropic API key.** Stored encrypted with `APP_SECRET`, never shown again, billed to them.
  - **Claude Code on the server computer.** Only for the owner. It uses the Claude Code login of the machine the
    server runs on: run `claude auth login` there in a terminal. The app never sees or stores that login.

Why Claude Code is owner-only: Anthropic does not allow an app to offer Claude.ai login to its users, or to route
requests through other people's Pro, Max or Ultra plans ([Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)).
Each reader of a shared server uses their own API key. Anyone who wants to use their own subscription runs their own copy of this app.

## Run on a server

The image holds the backend and the built frontend, so one address serves everything (no CORS, one cookie).
It runs on any Docker host: a cloud VM, Fly.io, Render, Railway. It does **not** run on Vercel or GitHub Pages:
the backend is a long-running process with background jobs and about 4.5 GB of search models.

On a VM with a domain name pointed at it:

```bash
cp .env.example .env          # then fill in APP_URL, OWNER_EMAIL, APP_SECRET, POSTGRES_PASSWORD, DOMAIN, SMTP_*
docker compose --profile app --profile https up -d --build
docker compose logs -f app    # sign-in links are here until SMTP is set up
```

Caddy (the `https` profile) gets a free HTTPS certificate for `DOMAIN`. Without a domain, leave that profile out and
put the app behind your host's HTTPS. Postgres, Qdrant and the app listen only on 127.0.0.1.

The image runs search on the CPU. Making the search index of a book takes minutes there (seconds on a GPU); readers
can read and get pages explained meanwhile, only chat waits. The first book downloads the two search models
(about 4.5 GB, kept in the `appdata` volume). Plan for at least 4 GB of RAM on the server.

To use Claude Code inside the container (owner only): build with `--build-arg CLAUDE_CODE=1`, then
`docker compose exec app claude auth login`. (Not tested yet.)

## Settings (environment variables)

All of them are listed with explanations in [.env.example](.env.example). The ones for development:

- `INSECURE_DEV_LOGIN=1`: an email alone signs in, with no check. Only on 127.0.0.1, for testing.
- `LLM_MODEL` (default `sonnet`), `LLM_SMALL_MODEL` (default `haiku`), `LLM_EFFORT` (API only, default `low`).
- `PREMAKE_PAGES`: page cards made ahead of the reader. Default `2`; `0` saves LLM usage.
- `DATABASE_URL`, `QDRANT_URL`, `DATA_DIR`: where Postgres, the search store and the book files are.
- `LLM_PROVIDER` and `ANTHROPIC_API_KEY`: only for the scripts below. In the app, each reader's own connection is used.

On a GPU, BGE-M3 and the reranker use about 2.2 GB of VRAM together.

## Checks

```powershell
cd backend
..\.venv\Scripts\python.exe llm.py                          # one real LLM call
..\.venv\Scripts\python.exe parsing.py                      # chapter cutting
..\.venv\Scripts\python.exe search.py                       # chunking, no models needed
..\.venv\Scripts\python.exe reader_profile.py               # the rules that change a reader's level
..\.venv\Scripts\python.exe eval_retrieval.py make --book 5 # write 50 test questions (LLM, once)
..\.venv\Scripts\python.exe eval_retrieval.py run --book 5  # Recall@5 and MRR for the 4 search modes
```

Results are saved in `eval/`.
