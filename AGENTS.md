# Hera vault guidance for Codex

Hera stores personal notes in this vault's `wiki/` and indexes them in `hera.db`.
The user owns these notes. Read relevant pages before using them, and cite a
page you rely on as `(Source: [[Title]])` so Hera can score useful notes.

The `hera-*` skills are linked from the vault into `~/.codex/skills/`. Read the
skill before using its engine. Resolve the vault from `~/.codex/hera.env`
(which points to the same locator as `~/.claude/hera.env`) or from this file's
repository. Never overwrite an unresolved conflict or push private notes to a
team remote without the user's explicit request.

Codex hooks load recent context, retrieve relevant pages, score final citations,
and file completed sessions. `HERA_OFF=1` disables those hooks for one process.
For manual ingest or publish engine calls from Codex, set
`HERA_LLM_BACKEND=codex` in the command environment so extraction runs through
Codex. The session filing hook sets this automatically.
Use `python scripts/hera_cli.py hera_db --doctor` to inspect vault health.
