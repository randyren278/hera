"""embed.py — Ollama nomic-embed-text client.

Contract:
  embed(text: str) -> list[float]      # 768-dim
  embed_batch(texts) -> list[list[float]]
  pack(vec) -> bytes                    # for sqlite-vec FLOAT[768] blobs

Design:
  - default endpoint http://localhost:11434 (Ollama)
  - retries: 3 with backoff on network errors; raise on final failure
  - fail-open policy lives in callers (§8.2 says the prompt-inject hook
    swallows Ollama errors and prints nothing); embed.py itself raises.
"""
from __future__ import annotations

import json
import os
import struct
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("HERA_EMBED_MODEL", "nomic-embed-text")
DIM = 768
RETRIES = 3


class EmbedError(RuntimeError):
    pass


def _post(endpoint: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_err = None
    for i in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.5 * (2 ** i))
    raise EmbedError(f"ollama {endpoint} failed after {RETRIES} tries: {last_err}")


def embed(text: str) -> list[float]:
    r = _post("/api/embeddings", {"model": MODEL, "prompt": text})
    v = r.get("embedding") or []
    if len(v) != DIM:
        raise EmbedError(f"expected {DIM}-dim embedding, got {len(v)}")
    return v


def embed_batch(texts: list[str]) -> list[list[float]]:
    # Ollama's /api/embeddings takes a single prompt; loop.
    return [embed(t) for t in texts]


def pack(vec: list[float]) -> bytes:
    if len(vec) != DIM:
        raise EmbedError(f"pack expected {DIM}-dim, got {len(vec)}")
    return struct.pack(f"{DIM}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{DIM}f", blob))
