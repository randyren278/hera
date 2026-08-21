# Hera CI and Documentation Audit Baseline

## Audit objective

Validate that claims made in README and documentation correspond to implemented behavior, while adding repeatable CI evidence.

## Current repository observations

- The project is a Python application with a substantial regression test suite.
- Existing tests cover installer behavior, hooks, indexing/search behavior, and safety-related workflows.
- Documentation describes a local-first Claude Code integration, SQLite-backed indexing, Ollama embeddings, install/uninstall flows, team staging, pruning, and conflict workflows.

## CI guarantees added

GitHub Actions now validates:

- Python regression tests through pytest.
- Existing shell regression scripts.
- Secret leakage using gitleaks.

## Documentation verification checklist

Future feature audits should verify each README claim against:

1. A unit test proving the behavior.
2. An integration/regression test exercising the workflow.
3. A documented limitation where behavior is intentionally manual or gated.

## Areas requiring continued validation

- Installer claims across macOS, Linux, and Windows.
- Claude Code hook lifecycle behavior.
- Ollama availability and failure handling.
- Team publish safety gates.
- Prune/archive restoration guarantees.
- Search ranking and embedding fallback behavior.

## Standard going forward

Documentation should describe only behavior backed by tests or clearly marked experimental functionality.
