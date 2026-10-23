# AGENTS.md

calldibs is a small CLI with no runtime dependencies. Keep it that way: standard library only, Python 3.10+.

- Code is in `src/calldibs/cli.py`, tests in `tests/test_cli.py` (unittest, no network).
- Set up with `uv sync`.
- Before you commit, run:
  - `uv run ruff format src tests`
  - `uv run ruff check src tests`
  - `uv run python -m unittest discover -s tests`
- Commit messages are in English and follow Conventional Commits (`feat:`, `fix:`, `docs:`).
- Everything calldibs reads from GitHub comes from one GraphQL request built in `build_query`. Add fields there and keep it to one request.
- The logic (`assess`, `rule_findings`, `claim_line`) takes plain dicts, so tests build fixtures instead of calling GitHub. Add a test for every new finding.
- Text output and `--json` carry the same findings. Exit status stays 0 for clear, 1 for stop or look, 2 for errors; scripts and agents depend on it.
- `skills/calldibs/SKILL.md` tells agents how to use the tool. Update it when options or output change. Keep it at that path: the `skills` CLI (`npx skills add bercedev/calldibs`) finds it there.
