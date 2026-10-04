"""The injection relevance gate, pinned without Ollama (fake vectors with
chosen cosines). Lowering inject_min_cosine or dropping the keyword rule fails
here, on every CI leg."""
from __future__ import annotations

import importlib
import io
import json
import math
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
import embed  # noqa: E402
import hera_db  # noqa: E402

# title -> (cosine to the query, contains the query keyword?)
PAGES = {
    "Strong Match": (0.80, True),
    "Edge Match": (0.66, True),       # just over 0.65, keyword → injected
    "Semantic Only": (0.70, False),   # no keyword and < 0.72 → withheld
    "Very Close": (0.75, False),      # no keyword but >= 0.72 → injected
    "Weak Match": (0.60, True),       # under 0.65 → withheld
}


def _fake_embed(text, **_):
    v = [0.0] * embed.DIM
    if text.startswith(embed.QUERY_PREFIX):
        v[0] = 1.0
        return v
    for i, (title, (c, _kw)) in enumerate(PAGES.items(), start=1):
        if title in text:
            v[0], v[i] = c, math.sqrt(1 - c * c)
            return v
    v[50] = 1.0
    return v


@pytest.fixture
def run_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERA_VAULT", str(tmp_path))
    monkeypatch.setattr(embed, "embed", _fake_embed)
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    conn.execute("CREATE TABLE IF NOT EXISTS pages_fts_map (rowid INTEGER PRIMARY KEY, "
                 "page_id TEXT NOT NULL UNIQUE)")
    for i, (title, (_c, kw)) in enumerate(PAGES.items()):
        body = ("zebra " if kw else "") + "notes [[Inner]] `code`\nsecond line"
        rel = f"wiki/concepts/{title}.md"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(f"---\nid: P{i}\n---\n{body}\n")
        conn.execute("INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at) "
                     "VALUES (?,?,'[]','concept',?,'t','t')", (f"P{i}", title, rel))
        cur = conn.execute("INSERT INTO pages_fts(title, body) VALUES (?, ?)", (title, body))
        conn.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)", (cur.lastrowid, f"P{i}"))
        conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
                     (f"P{i}", embed.pack(embed.embed_document(f"{title}\n{body}"))))
    conn.execute("UPDATE config SET value='10' WHERE key='inject_top_n'")
    conn.commit()
    monkeypatch.setattr(hera_db, "connect", lambda *a, **k: conn)
    import team_index
    monkeypatch.setattr(team_index, "TEAM_DB", tmp_path / "no-team.db")
    import prompt_inject
    pi = importlib.reload(prompt_inject)

    def run(prompt):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))
        capsys.readouterr()
        assert pi.main() == 0
        return capsys.readouterr().out
    return run


def test_gate_boundaries(run_hook):
    out = run_hook("tell me about zebra handling")
    for title in ("Strong Match", "Edge Match", "Very Close"):
        assert f"[[{title}]]" in out, (title, out)
    for title in ("Semantic Only", "Weak Match"):
        assert f"[[{title}]]" not in out, (title, out)


def test_pointer_text_cannot_inject_markup(run_hook):
    out = run_hook("tell me about zebra handling")
    line = next(l for l in out.splitlines() if "[[Strong Match]]" in l)
    tail = line.split("—", 1)[1]
    assert "[[" not in tail and "`" not in tail
