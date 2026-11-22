"""Chunk, embed, index and search. The embedder, the reranker and Qdrant are known only here.

GPU memory (8 GB laptop): BGE-M3 and the reranker in half precision peak at about 2.2 GB together
with batch size 16. Lower BATCH if another program is using the GPU.
"""
import os
import threading

from qdrant_client import QdrantClient, models

QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION = "chunks"
EMBED_MODEL = "BAAI/bge-m3"
# ponytail: the design names Qwen3-Reranker-0.6B. This one is the same size and comes with the
# embedder's library. Swap it here if the retrieval test says Qwen3 is better.
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
CHUNK_WORDS = 300   # about 400 tokens
BATCH = 16
CANDIDATES = 30     # chunks taken from each search before fusion and reranking
MODES = ("dense", "sparse", "hybrid", "hybrid_rerank")

client = QdrantClient(url=QDRANT_URL, timeout=30)
gpu_lock = threading.Lock()  # one model call at a time on the GPU
_models = {}


def _embedder():
    if "embed" not in _models:
        from FlagEmbedding import BGEM3FlagModel  # slow import, only when first needed
        _models["embed"] = BGEM3FlagModel(EMBED_MODEL, use_fp16=True)
    return _models["embed"]


def _reranker():
    if "rerank" not in _models:
        from FlagEmbedding import FlagReranker
        _models["rerank"] = FlagReranker(RERANK_MODEL, use_fp16=True)
    return _models["rerank"]


def embed(texts: list[str]) -> tuple[list[list[float]], list[models.SparseVector]]:
    with gpu_lock:
        out = _embedder().encode(texts, batch_size=BATCH, max_length=1024, return_dense=True, return_sparse=True)
    sparse = [models.SparseVector(indices=[int(t) for t in w], values=[float(v) for v in w.values()])
              for w in out["lexical_weights"]]
    return out["dense_vecs"].tolist(), sparse


# ---- chunking (no models) --------------------------------------------------

def heading_paths(toc: list, n_pages: int) -> list[str]:
    """toc is [[level, title, start_page], ...]. Returns the heading path for each page, 1..n_pages."""
    paths, stack, i = [], [], 0
    for page in range(1, n_pages + 1):
        while i < len(toc) and toc[i][2] <= page:
            level, title = toc[i][0], toc[i][1]
            stack[level - 1:] = [title.strip()]
            i += 1
        paths.append(" > ".join(stack))
    return paths


def chunk_page(text: str) -> list[str]:
    # ponytail: chunks never cross a page, so each chunk has one exact page to cite. A sentence that
    # runs over a page break is cut in two. Join pages by section if the retrieval test shows misses.
    chunks, lines, words = [], [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        lines.append(line)
        words += len(line.split())
        if words >= CHUNK_WORDS:
            chunks.append("\n".join(lines))
            lines, words = [], 0
    if lines:
        chunks.append("\n".join(lines))
    return chunks


# ---- index -----------------------------------------------------------------

def _ensure_collection() -> None:
    if client.collection_exists(COLLECTION):
        return
    client.create_collection(
        COLLECTION,
        vectors_config={"dense": models.VectorParams(size=1024, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    for field in ("user_id", "book_id", "page_no"):
        client.create_payload_index(COLLECTION, field, models.PayloadSchemaType.INTEGER)


def _match(field: str, value: int) -> models.FieldCondition:
    return models.FieldCondition(key=field, match=models.MatchValue(value=value))


def delete(field: str, value: int) -> None:
    """Remove every chunk of one book ("book_id") or one user ("user_id")."""
    if client.collection_exists(COLLECTION):
        client.delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[_match(field, value)])))


def index_book(user_id: int, book_id: int, pages: list[tuple[int, str]], toc: list) -> int:
    """Embed and store every chunk of a book. Starts from zero each time, so it is safe to run again."""
    _ensure_collection()
    delete("book_id", book_id)
    paths = heading_paths(toc, max((n for n, _ in pages), default=0))
    chunks = [{"user_id": user_id, "book_id": book_id, "page_no": n, "heading_path": paths[n - 1], "text": c}
              for n, text in pages for c in chunk_page(text)]
    for start in range(0, len(chunks), 64):
        part = chunks[start:start + 64]
        # The heading path goes in front of the text: free context for the embedding (design 6.4).
        dense, sparse = embed([f"{c['heading_path']}\n{c['text']}" for c in part])
        client.upsert(COLLECTION, [
            # ponytail: the id is book id + position. The chunk text lives in the Qdrant payload,
            # not in a Postgres table; the index can always be rebuilt from the pages table.
            models.PointStruct(id=book_id * 1_000_000 + start + i, payload=c,
                               vector={"dense": dense[i], "sparse": sparse[i]})
            for i, c in enumerate(part)
        ])
    return len(chunks)


# ---- search ----------------------------------------------------------------

def search(query: str, user_id: int, book_id: int, max_page: int | None = None,
           mode: str = "hybrid_rerank", k: int = 5, min_page: int | None = None) -> list[dict]:
    """Returns up to k chunks: {page_no, heading_path, text, score}, best first.
    max_page: only pages the reader has already seen. min_page with max_page: one chapter."""
    must = [_match("user_id", user_id), _match("book_id", book_id)]
    if max_page is not None or min_page is not None:
        must.append(models.FieldCondition(key="page_no", range=models.Range(gte=min_page, lte=max_page)))
    flt = models.Filter(must=must)
    (dense,), (sparse,) = embed([query])

    if mode == "dense":
        res = client.query_points(COLLECTION, query=dense, using="dense", query_filter=flt, limit=CANDIDATES)
    elif mode == "sparse":
        res = client.query_points(COLLECTION, query=sparse, using="sparse", query_filter=flt, limit=CANDIDATES)
    else:
        res = client.query_points(
            COLLECTION,
            prefetch=[models.Prefetch(query=dense, using="dense", filter=flt, limit=CANDIDATES),
                      models.Prefetch(query=sparse, using="sparse", filter=flt, limit=CANDIDATES)],
            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=CANDIDATES)
    hits = [{"page_no": p.payload["page_no"], "heading_path": p.payload["heading_path"],
             "text": p.payload["text"], "score": p.score} for p in res.points]

    if mode == "hybrid_rerank" and hits:
        with gpu_lock:
            scores = _reranker().compute_score([[query, h["text"]] for h in hits], normalize=True)
        for h, s in zip(hits, scores):
            h["score"] = float(s)
        hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:k]


if __name__ == "__main__":
    # Self-check of the parts that need no model and no Qdrant. Run: python search.py
    toc = [[1, "Ch 1", 2], [2, "Limits", 2], [2, "Rules", 4], [1, "Ch 2", 5]]
    assert heading_paths(toc, 5) == ["", "Ch 1 > Limits", "Ch 1 > Limits", "Ch 1 > Rules", "Ch 2"]
    assert heading_paths([], 2) == ["", ""]
    parts = chunk_page("\n".join(["word " * 100] * 7) + "\n\n")
    assert [len(p.split()) for p in parts] == [300, 300, 100], [len(p.split()) for p in parts]
    assert chunk_page("  \n\n") == []
    print("ok")
