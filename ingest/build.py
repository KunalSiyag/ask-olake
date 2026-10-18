"""Build step: scrape + collect sources, chunk them, write data/chunks.jsonl and data/meta.json.

Run: uv run python -m ingest.build
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ingest import docs, issues, repos
from ingest.chunk import make_chunks

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "minishlab/potion-base-8M")

OUT = Path("data")


def main() -> None:
    sections = docs.scrape() + repos.collect() + issues.collect()
    chunks = make_chunks(sections)
    OUT.mkdir(exist_ok=True)
    with open(OUT / "chunks.jsonl", "w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    if EMBEDDING_MODEL:
        import numpy as np
        from model2vec import StaticModel

        from app.search import embed_text

        vecs = StaticModel.from_pretrained(EMBEDDING_MODEL).encode([embed_text(c) for c in chunks])
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)
        np.save(OUT / "embeddings.npy", vecs.astype(np.float16))
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunks": len(chunks),
        "pages": len({c["page_url"] for c in chunks}),
        "by_source": Counter(c["source_type"] for c in chunks),
        "embedding_model": EMBEDDING_MODEL or None,
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
