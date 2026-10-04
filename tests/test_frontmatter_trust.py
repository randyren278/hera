#!/usr/bin/env python3
"""CP-2.3 — frontmatter carries the trust tier, and ingest persists it.

What this proves:
  - _frontmatter() emits `trust: <tier>` as an UNQUOTED scalar, on its own
    line, exactly like visibility/source_kind/owner
  - all three tiers round-trip through a YAML parse as plain strings
  - a value that is not identifier-shaped still gets quoted (the unquoting is
    a whitelist on the key, not a blanket bypass of the quoting rule)
  - PageWrite defaults to 'self', and ingest_source rejects a bogus tier
  - _upsert_page persists pw.trust and refuses an illegal one
  - _write_page_file stamps trust into the file even when the caller passed no
    extra frontmatter — so a reindex from markdown cannot lose the tier
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _trust_helpers import REPO, run_tests  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import ingest  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - preflight guarantees pyyaml
    yaml = None


def _fm_lines(**extra):
    return ingest._frontmatter("01X", "T", "concept", extra=extra).splitlines()


def test_trust_emitted_unquoted_for_every_tier():
    for tier in hera_db.TRUST_TIERS:
        lines = _fm_lines(trust=tier)
        assert f"trust: {tier}" in lines, (
            f"expected an unquoted `trust: {tier}` line, got: {lines}")
        assert f'trust: "{tier}"' not in lines, f"tier {tier} was quoted"


def test_trust_parses_as_a_plain_string():
    if yaml is None:
        return
    for tier in hera_db.TRUST_TIERS:
        raw = ingest._frontmatter("01X", "T", "concept", extra={"trust": tier})
        doc = yaml.safe_load(raw.strip().strip("-").strip()) or {}
        # Re-parse via the full fenced form to be sure we read the real block.
        body = raw.split("---")[1]
        doc = yaml.safe_load(body)
        assert doc["trust"] == tier, f"YAML round-trip gave {doc.get('trust')!r}"
        assert isinstance(doc["trust"], str)


def test_unquoting_is_keyed_not_blanket():
    """A non-identifier-shaped value must still be quoted — otherwise the
    whitelist would be a hole through which arbitrary YAML could be injected."""
    lines = _fm_lines(trust="not a tier: with a colon")
    assert 'trust: "not a tier: with a colon"' in lines, (
        f"non-identifier value was emitted unquoted: {lines}")


def test_other_keys_are_still_quoted():
    """Guard against the whitelist being widened by accident."""
    lines = _fm_lines(some_other_key="value")
    assert 'some_other_key: "value"' in lines, lines


def test_pagewrite_defaults_to_self():
    pw = ingest.PageWrite(id="01D", title="T", type="concept",
                          path=pathlib.Path("/tmp/x.md"), body_md="b")
    assert pw.trust == "self", f"PageWrite default tier is {pw.trust!r}"


def test_ingest_source_signature_accepts_trust():
    import inspect
    sig = inspect.signature(ingest.ingest_source)
    assert "trust" in sig.parameters, "ingest_source() has no trust parameter"
    assert sig.parameters["trust"].default == "self", (
        f"ingest_source trust default is {sig.parameters['trust'].default!r}")


def test_ingest_source_rejects_bogus_tier():
    try:
        ingest.ingest_source("/nonexistent/source.md", trust="evil")
    except ValueError as e:
        assert "evil" in str(e)
        return
    except Exception as e:  # pragma: no cover
        raise AssertionError(
            f"bogus tier raised {type(e).__name__}, not ValueError: {e}")
    raise AssertionError("ingest_source accepted trust='evil'")


def test_upsert_persists_trust():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        conn = hera_db.ensure_ready(root / "hera.db")
        for tier in hera_db.TRUST_TIERS:
            pw = ingest.PageWrite(id=f"01P{tier}", title=f"T {tier}",
                                  type="concept",
                                  path=ingest.REPO / "wiki" / f"{tier}.md",
                                  body_md="b", trust=tier)
            ingest._upsert_page(conn, pw)
        conn.commit()
        got = dict(conn.execute("SELECT id, trust FROM pages").fetchall())
        for tier in hera_db.TRUST_TIERS:
            assert got[f"01P{tier}"] == tier, f"{tier} stored as {got[f'01P{tier}']!r}"


def test_upsert_rejects_bogus_tier():
    with tempfile.TemporaryDirectory() as d:
        conn = hera_db.ensure_ready(pathlib.Path(d) / "hera.db")
        pw = ingest.PageWrite(id="01BAD", title="Bad", type="concept",
                              path=ingest.REPO / "wiki" / "bad.md",
                              body_md="b", trust="evil")
        try:
            ingest._upsert_page(conn, pw)
        except ValueError:
            return
        raise AssertionError("_upsert_page accepted trust='evil'")


def test_written_file_carries_trust_without_extra_frontmatter():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        conn = hera_db.ensure_ready(root / "hera.db")
        target = root / "page.md"
        pw = ingest.PageWrite(id="01W", title="Written", type="concept",
                              path=target, body_md="hello", trust="untrusted")
        ingest._write_page_file(conn, pw)          # note: no extra_fm passed
        text = target.read_text(encoding="utf-8")
        assert "\ntrust: untrusted\n" in text, (
            f"file written without a trust line:\n{text}")


if __name__ == "__main__":
    sys.exit(run_tests(dict(globals())))
