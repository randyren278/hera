#!/usr/bin/env python3
"""CP-2.4 — explicit opt-in still works.

The filter must be a filter, not a delete. A caller that names 'untrusted'
gets untrusted pages: that is how a future quarantined reader (spec §5.1's
dual-LLM runtime) reads the pages it is allowed to read. What must never
happen is opting in *by accident*.

So this file proves both directions:
  - naming the tier returns it, with correct paths and tiers
  - the opt-in is per-call and does not leak into the next default call
  - the module-level default is a tuple, not a mutable shared list a caller
    could append 'untrusted' onto and poison for every other caller
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_ollama  # real embeddings; see conftest.py

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _trust_helpers import REPO, Vault, run_tests  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import search as _search  # noqa: E402

QUERY = "solar panel inverter efficiency curve"
BODY = "The solar panel inverter efficiency curve peaks near nominal load."


def _vault(tmp: pathlib.Path) -> Vault:
    v = Vault(tmp)
    v.add_page("Untrusted Inverter", BODY + " From an unverified email.",
               trust="untrusted")
    v.add_page("Trusted Inverter", BODY + " Measured on the bench.", trust="self")
    return v


def test_explicit_opt_in_returns_untrusted():
    with tempfile.TemporaryDirectory() as d:
        v = _vault(pathlib.Path(d))
        hits = _search.hybrid_search(v.conn, QUERY, top_n=10, floor=0.0,
                                     trust_in=("self", "team", "untrusted"))
        titles = {h.title for h in hits}
        assert "Untrusted Inverter" in titles, (
            f"explicit opt-in did not return the untrusted page: {titles}")
        assert "Trusted Inverter" in titles, titles


def test_untrusted_only_opt_in():
    with tempfile.TemporaryDirectory() as d:
        v = _vault(pathlib.Path(d))
        hits = _search.hybrid_search(v.conn, QUERY, top_n=10, floor=0.0,
                                     trust_in=("untrusted",))
        assert {h.title for h in hits} == {"Untrusted Inverter"}, \
            {h.title for h in hits}


def test_opted_in_hits_are_fully_populated():
    """An opted-in hit is a real Hit — path, score and tier all present — so a
    quarantined reader can actually open the file it was handed."""
    with tempfile.TemporaryDirectory() as d:
        v = _vault(pathlib.Path(d))
        hit = _search.hybrid_search(v.conn, QUERY, top_n=10, floor=0.0,
                                    trust_in=("untrusted",))[0]
        assert hit.trust == "untrusted", f"tier reported as {hit.trust!r}"
        assert hit.path.endswith(".md"), f"bad path {hit.path!r}"
        assert (pathlib.Path(d) / hit.path).exists(), f"path does not resolve: {hit.path}"
        assert hit.score > 0, hit.score


def test_opt_in_does_not_persist_to_the_next_call():
    with tempfile.TemporaryDirectory() as d:
        v = _vault(pathlib.Path(d))
        _search.hybrid_search(v.conn, QUERY, top_n=10, floor=0.0,
                              trust_in=("self", "team", "untrusted"))
        after = {h.title for h in
                 _search.hybrid_search(v.conn, QUERY, top_n=10, floor=0.0)}
        assert "Untrusted Inverter" not in after, (
            f"an opted-in call leaked into the following default call: {after}")


def test_default_is_an_immutable_tuple():
    assert isinstance(_search.DEFAULT_TRUST, tuple), (
        f"DEFAULT_TRUST is {type(_search.DEFAULT_TRUST).__name__}; a mutable "
        "default could be appended to and would silently open the filter "
        "for every caller in the process")
    assert _search.DEFAULT_TRUST == ("self", "team")


if __name__ == "__main__":
    sys.exit(run_tests(dict(globals())))
