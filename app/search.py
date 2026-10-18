"""Hybrid retrieval: BM25 + model2vec embeddings, fused with Reciprocal Rank Fusion,
then a cheap heuristic rerank.

CLI: uv run python -m app.search "how do I enable CDC for postgres?"
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

DATA = Path(os.getenv("DATA_DIR", "data"))
DEFAULT_EMBEDDING_MODEL = "minishlab/potion-base-8M"  # set EMBEDDING_MODEL="" for BM25-only
RRF_K = 60
EMB_WEIGHT = float(os.getenv("EMB_WEIGHT", "0.5"))  # model2vec is weaker than BM25 on this corpus (see eval)
SOURCE_WEIGHT = {"docs": 1.0, "repo": float(os.getenv("REPO_WEIGHT", "0.7")), "blog": float(os.getenv("BLOG_WEIGHT", "0.5")), "issue": 0.7}
FUSION_TERMS = {"fusion", "compaction", "compact", "optimizer", "optimizers", "maintenance", "amoro", "snapshot", "expire", "orphan"}
STOPWORDS = set(
    "a an the is are was were be been do does did how what why when where which who can could should would "
    "i me my we our you your it its to of in on for with and or not this that these those there from by as at "
    "if then so up use using used have has had get set into about any all".split()
)


_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "ies", "ied", "ed", "es", "s")


def _stem(tok: str) -> str:
    """Very light suffix stripping, added as an extra token next to the original word."""
    if not tok.isalpha() or len(tok) < 5:
        return tok
    for suf in _SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            base = tok[: -len(suf)]
            return base + "y" if suf in ("ies", "ied") else base
    return tok


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; identifiers like max_threads or jdbc_url_params are kept whole
    and also split into their parts so either form matches."""
    tokens = []
    for tok in re.findall(r"[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]", text.lower()):
        if tok in STOPWORDS:
            continue
        tokens.append(tok)
        stem = _stem(tok)
        if stem != tok:
            tokens.append(stem)  # "partitioning"/"partitions" also match "partition"
        parts = re.split(r"[_.\-]", tok)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p and p not in STOPWORDS)
    return tokens


def embed_text(chunk: dict) -> str:
    return f"{chunk['title']}\n{chunk['text']}"


@dataclass
class Hit:
    chunk: dict
    score: float
    bm25: float
    cosine: float


def text_key(chunk: dict) -> str:
    return hashlib.sha1(embed_text(chunk).encode()).hexdigest()


_models: dict = {}


def load_model(name: str):
    if name not in _models:
        from model2vec import StaticModel
        _models[name] = StaticModel.from_pretrained(name)
    return _models[name]


def embed_chunks(model, chunks: list[dict]) -> np.ndarray:
    vecs = model.encode([embed_text(c) for c in chunks]).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)


class SearchIndex:
    """Built from data/ on disk, or from in-memory chunks during a live refresh.
    `previous` lets a rebuild reuse embeddings of unchanged chunks."""

    def __init__(self, data_dir: Path = DATA, embedding_model: str | None = None,
                 chunks: list[dict] | None = None, previous: "SearchIndex | None" = None):
        from_disk = chunks is None
        self.chunks = [json.loads(line) for line in open(data_dir / "chunks.jsonl")] if from_disk else chunks
        self.bm25 = BM25Okapi([tokenize(embed_text(c)) for c in self.chunks])
        self.title_tokens = [set(tokenize(c["title"])) for c in self.chunks]

        self.model, self.vectors = None, None
        model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL) if embedding_model is None else embedding_model
        emb_path = data_dir / "embeddings.npy"
        if not model_name or (from_disk and not emb_path.exists()):
            return
        self.model = load_model(model_name)
        if from_disk:
            self.vectors = np.load(emb_path).astype(np.float32)
            return
        known = previous.vectors_by_key() if previous is not None and previous.vectors is not None else {}
        missing = [c for c in self.chunks if text_key(c) not in known]
        if missing:
            known.update(zip((text_key(c) for c in missing), embed_chunks(self.model, missing)))
        self.vectors = np.stack([known[text_key(c)] for c in self.chunks]) if self.chunks else None

    def vectors_by_key(self) -> dict[str, np.ndarray]:
        return {text_key(c): v for c, v in zip(self.chunks, self.vectors)}

    def search(self, query: str, k: int = 8, per_page: int = 2, pool: int = 50) -> list[Hit]:
        q_tokens = tokenize(query)
        bm25 = self.bm25.get_scores(q_tokens) if q_tokens else np.zeros(len(self.chunks))
        rankings = [np.argsort(-bm25)[:pool]]

        cosine = np.zeros(len(self.chunks))
        if self.model is not None:
            q = self.model.encode([query])[0]
            q = q / (np.linalg.norm(q) or 1.0)
            cosine = self.vectors @ q
            rankings.append(np.argsort(-cosine)[:pool])

        # Reciprocal Rank Fusion: each list contributes 1/(k + rank)
        fused: dict[int, float] = {}
        for weight, ranking in zip((1.0, EMB_WEIGHT), rankings):
            for rank, idx in enumerate(ranking):
                fused[int(idx)] = fused.get(int(idx), 0.0) + weight / (RRF_K + rank + 1)

        # Heuristic rerank: title overlap, source trust, product hint
        q_set = set(q_tokens)
        wants_fusion = bool(q_set & FUSION_TERMS)
        scored = []
        for idx, s in fused.items():
            c = self.chunks[idx]
            title_overlap = len(q_set & self.title_tokens[idx]) / len(q_set) if q_set else 0.0
            s *= 1.0 + 0.5 * title_overlap
            s *= SOURCE_WEIGHT.get(c["source_type"], 0.7)
            if wants_fusion and c["product"] == "fusion":
                s *= 1.2
            scored.append((s, idx))
        scored.sort(reverse=True)

        hits, per_page_count = [], {}
        for s, idx in scored:
            page = self.chunks[idx]["page_url"]
            if per_page_count.get(page, 0) >= per_page:
                continue
            per_page_count[page] = per_page_count.get(page, 0) + 1
            hits.append(Hit(self.chunks[idx], s, float(bm25[idx]), float(cosine[idx])))
            if len(hits) == k:
                break
        return hits


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    index = SearchIndex()
    for hit in index.search(" ".join(sys.argv[1:])):
        print(f"{hit.score:.4f} bm25={hit.bm25:5.1f} cos={hit.cosine:.2f} [{hit.chunk['source_type']}] {hit.chunk['title']}\n        {hit.chunk['url']}")
