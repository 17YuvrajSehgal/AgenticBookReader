"""The retrieval test from design section 16.

  python eval_retrieval.py make --book 5     # write 50 questions with the LLM (once per book)
  python eval_retrieval.py run  --book 5     # Recall@5 and MRR@10 for each search mode

Limits of this test, so the numbers are read correctly:
- The questions are written by an LLM that looks at the gold page. They may share more words with
  that page than a real reader's question would. This helps sparse search the most.
- Only one page counts as correct. If the same fact is also on another page, a good hit is scored as a miss.
  The "near" columns accept the page before and after, because text runs over page breaks.
"""
import argparse
import json
import random
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

import llm
import search
from app import DATABASE_URL

SEED = 0
N_QUESTIONS = 50
PAGES_PER_CALL = 10
OUT = Path(__file__).parent.parent / "eval"

QUESTION_SYSTEM = """You write test questions for a book search tool.
For each page you are given, write one question that a reader could ask weeks later, after they forgot a detail.

Rules:
- The answer must be clearly on that page.
- The question must make sense alone. Never say "this page", "the author", "the text" or "the example above".
- Use your own words. Do not copy more than three words in a row from the page.
- Ask about a specific fact or idea, not about the page in general.
- Return one question per page, with the same page number you were given."""

class Question(BaseModel):
    page: int
    question: str


class Questions(BaseModel):
    questions: list[Question]


def make(book_id: int, db) -> None:
    n = db.execute("SELECT page_count FROM books WHERE id = %s", (book_id,)).fetchone()["page_count"]
    # Skip the front matter and the index at the back, and pages with little text.
    rows = db.execute("SELECT page_no, text_md FROM pages WHERE book_id = %s AND page_no BETWEEN %s AND %s "
                      "AND length(text_md) > 800 ORDER BY page_no", (book_id, int(n * 0.05), int(n * 0.88))).fetchall()
    pages = sorted(random.Random(SEED).sample(rows, N_QUESTIONS), key=lambda r: r["page_no"])
    questions = []
    for i in range(0, len(pages), PAGES_PER_CALL):
        part = pages[i:i + PAGES_PER_CALL]
        prompt = "\n\n".join(f'<page number="{p["page_no"]}">\n{p["text_md"]}\n</page>' for p in part)
        out, _ = llm._call(QUESTION_SYSTEM, prompt, Questions, llm.MODEL)
        wanted = {p["page_no"] for p in part}
        questions += [x.model_dump() for x in out.questions if x.page in wanted]
        print(f"{len(questions)} questions")
    OUT.mkdir(exist_ok=True)
    (OUT / f"questions_book{book_id}.json").write_text(json.dumps(questions, indent=2), encoding="utf-8")


def run(book_id: int, db) -> None:
    user_id = db.execute("SELECT user_id FROM books WHERE id = %s", (book_id,)).fetchone()["user_id"]
    questions = json.loads((OUT / f"questions_book{book_id}.json").read_text(encoding="utf-8"))
    search.search("warm up", user_id, book_id)  # load both models before timing
    results, details = {}, []
    for mode in search.MODES:
        ranks, near_ranks, t0 = [], [], time.time()
        for x in questions:
            pages = [h["page_no"] for h in search.search(x["question"], user_id, book_id, mode=mode, k=10)]
            # Rank of the first correct chunk, 1-based. None = not in the top 10.
            ranks.append(next((i for i, p in enumerate(pages, 1) if p == x["page"]), None))
            near_ranks.append(next((i for i, p in enumerate(pages, 1) if abs(p - x["page"]) <= 1), None))
            details.append({"mode": mode, **x, "top_pages": pages, "rank": ranks[-1]})
        results[mode] = {
            "recall@5": sum(r is not None and r <= 5 for r in ranks) / len(ranks),
            "mrr@10": sum(1 / r for r in ranks if r) / len(ranks),
            "recall@5_near": sum(r is not None and r <= 5 for r in near_ranks) / len(ranks),
            "mrr@10_near": sum(1 / r for r in near_ranks if r) / len(ranks),
            "ms_per_query": round((time.time() - t0) / len(questions) * 1000),
        }
    out = {"book_id": book_id, "n_questions": len(questions), "embedder": search.EMBED_MODEL,
           "reranker": search.RERANK_MODEL, "chunk_words": search.CHUNK_WORDS, "results": results, "details": details}
    (OUT / f"results_book{book_id}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{'mode':<15}{'R@5':>7}{'MRR@10':>8}{'R@5 near':>10}{'MRR near':>10}{'ms/query':>10}")
    for mode, r in results.items():
        print(f"{mode:<15}{r['recall@5']:>7.2f}{r['mrr@10']:>8.2f}{r['recall@5_near']:>10.2f}"
              f"{r['mrr@10_near']:>10.2f}{r['ms_per_query']:>10}")


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("command", choices=["make", "run"])
    args.add_argument("--book", type=int, required=True)
    args = args.parse_args()
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as db:
        {"make": make, "run": run}[args.command](args.book, db)
