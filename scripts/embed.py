"""embed.py — Ollama nomic-embed-text client.

Contract:
  embed(text: str) -> list[float]      # 768-dim, raw model output
  embed_document(text) / embed_query(text)
                                        # what the index stores / searches with:
                                        # nomic task prefix + unit length
  embed_batch(texts) -> list[list[float]]
  pack(vec) -> bytes                    # for sqlite-vec FLOAT[768] blobs

Index scheme (SCHEME, recorded in config.embed_scheme): nomic-embed-text is
trained with "search_document: " / "search_query: " task prefixes, and the
vectors are scaled to unit length so sqlite-vec's L2 KNN ranks exactly by
cosine (cos = 1 - d^2/2). A database built under another scheme must be
re-embedded with scripts/reembed.py; doctor reports the mismatch.

Design:
  - default endpoint http://localhost:11434 (Ollama)
  - retries: 3 with backoff on network errors; raise on final failure
  - fail-open policy lives in callers (§8.2 says the prompt-inject hook
    swallows Ollama errors and prints nothing); embed.py itself raises.
"""
from __future__ import annotations

import json
import math
import os
import struct
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("HERA_EMBED_MODEL", "nomic-embed-text")
DIM = 768
RETRIES = 3
SCHEME = "nomic-prefixed-unit-v1"
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbedError(RuntimeError):
    pass


def _post(endpoint: str, payload: dict, timeout: float = 30.0,
          retries: int = RETRIES) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_err = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if i + 1 < retries:
                time.sleep(0.5 * (2 ** i))
    raise EmbedError(f"ollama {endpoint} failed after {retries} tries: {last_err}")


def embed(text: str, timeout: float = 30.0, retries: int = RETRIES) -> list[float]:
    r = _post("/api/embeddings", {"model": MODEL, "prompt": text},
              timeout=timeout, retries=retries)
    v = r.get("embedding") or []
    if len(v) != DIM:
        raise EmbedError(f"expected {DIM}-dim embedding, got {len(v)}")
    return v


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def embed_document(text: str, **kw) -> list[float]:
    """Vector for a page being indexed (hera.db or team.db)."""
    return _unit(embed(DOC_PREFIX + text, **kw))


def embed_query(text: str, **kw) -> list[float]:
    """Vector for a search query."""
    return _unit(embed(QUERY_PREFIX + text, **kw))


def cosine_from_distance(d: float) -> float:
    """sqlite-vec L2 distance between unit vectors -> cosine similarity."""
    return 1.0 - (d * d) / 2.0


def embed_batch(texts: list[str]) -> list[list[float]]:
    # Ollama's /api/embeddings takes a single prompt; loop.
    return [embed(t) for t in texts]


def pack(vec: list[float]) -> bytes:
    if len(vec) != DIM:
        raise EmbedError(f"pack expected {DIM}-dim, got {len(vec)}")
    return struct.pack(f"{DIM}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{DIM}f", blob))
