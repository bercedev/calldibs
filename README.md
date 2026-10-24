<p align="center">
  <img src="assets/banner.png" alt="calldibs: check before you fix" width="100%">
</p>

<p align="center">
  <b>Check that nobody is already working on a GitHub issue before you start a fix.</b>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-3fb950?style=flat-square">
  <a href="https://github.com/astral-sh/uv"><img alt="uv" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square"></a>
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square"></a>
  <a href="skills/calldibs/SKILL.md"><img alt="Agent skill" src="https://img.shields.io/badge/agent%20skill-included-d29922?style=flat-square"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
</p>

---

## ✨ Why

You find a bug, you fix it, you open a PR, and then you see that someone opened the same fix two days ago. Or the project wanted you to get assigned first. Or it doesn't take AI-written code at all.

Before I pick up an issue I want to know:

- 🔍 Is there already a PR for it?
- 🙋 Did someone say they'd take it?
- 👤 Is it assigned to someone?
- 📜 What does the project ask from contributors: a CLA, a DCO sign-off, an AI policy, an issue that has to be accepted first?

`calldibs` answers all of that with one GraphQL request.

```console
$ calldibs acme/widget#412
acme/widget#412  Crash when the config file is empty
  STOP  open PR #418 by alice: fix(config): handle an empty file
  LOOK  bob said they would take it on 2026-10-20: "I'd like to work on this"
  LOOK  issue gate: CONTRIBUTING.md:38: The issue must carry the `accepted` label before you open a PR.
  NOTE  closed PR #405 by carol: guard empty config; it was not merged, read why before redoing it
  NOTE  CLA: CONTRIBUTING.md:31: You need to sign a Contributor License Agreement (CLA) before we can accept your PR.
stop: someone is already on this, or it is not open for work
```

## 📦 Install

| With | Command |
| --- | --- |
| uv | `uv tool install git+https://github.com/bercedev/calldibs` |
| pipx | `pipx install git+https://github.com/bercedev/calldibs` |
| pip | `pip install git+https://github.com/bercedev/calldibs` |
| run once, no install | `uvx --from git+https://github.com/bercedev/calldibs calldibs owner/repo#123` |

It needs Python 3.10 or newer and nothing else.

🔑 It reads a token from `GH_TOKEN` or `GITHUB_TOKEN`. If neither is set, it asks `gh auth token`. Read access to public repositories is enough.

## 🧭 Usage

```bash
calldibs owner/repo#123                    # check an issue
calldibs https://github.com/owner/repo/issues/123   # links from notifications work too
calldibs owner/repo#123 -k crash           # also flag open PRs with "crash" in the title
calldibs owner/repo#123 --ignore 456       # skip PR #456 after you checked it by hand
calldibs owner/repo                        # only print the repository's contribution rules
calldibs owner/repo#123 --json             # JSON for scripts and agents
calldibs find --label "good first issue"   # look for something to work on
```

🔗 The exit status is `0` when the issue is clear, `1` when there is something to stop at or look at, and `2` on errors. So you can put it in front of a push:

```bash
calldibs owner/repo#123 -k crash && git push && gh pr create
```

An issue gate, a ban on AI-written code or a required AI disclosure in the project's rules counts as LOOK, so the chain stops there too. CLA and DCO lines are notes: read them, they don't stop the chain.

## 🔎 Find something to work on

`calldibs find` lists open issues that nobody seems to be working on: no assignee, no open PR, no blocking label, and nobody said they'd take it.

```bash
calldibs find --label "good first issue" --language python
calldibs find --label "help wanted" --min-stars 5000 --days 14
calldibs find crash --repo owner/repo --repo owner/other
calldibs find --min-stars 1000 --json
```

```console
$ calldibs find --label "good first issue" --min-stars 1000 --days 90
2 open issues nobody seems to be working on
     2805★  acme/widget#4093  0c  2026-08-10  Tags cannot be edited after an import  [bug, good first issue]
     2805★  acme/widget#4077  0c  2026-08-02  Design of shared posts  [good first issue, UI]
next: run `calldibs acme/widget#4093` on the one you pick, before you start
```

| Option | Default | What it does |
| --- | --- | --- |
| `words` | | Search words, like `crash` or `memory leak` |
| `--repo` | | Only these repositories |
| `--label` | | Only issues with this label |
| `--language` | | Only repositories in this language |
| `--min-stars` | `0` | Picks active repositories above the bar first, since GitHub's issue search can't filter by stars |
| `--days` | `30` | Only issues opened in the last N days |
| `--max-comments` | `3` | Only quiet issues |
| `--limit` | `10` | At most 30 |

Good issues go fast. If the list is empty, try a larger `--days` or `--max-comments`. Then pick one you'll really fix and run `calldibs owner/repo#123` on it before you start: `find` doesn't read the project's rules, the check does.

## 🚦 What it checks

| | Level | When |
| --- | --- | --- |
| 🛑 | **STOP** | The issue is closed, or the repository is archived |
| 🛑 | **STOP** | The issue is assigned to someone else |
| 🛑 | **STOP** | An open PR would close the issue (`Fixes #123`, or linked in the sidebar) |
| 👀 | **LOOK** | An open PR mentions the issue without closing it |
| 👀 | **LOOK** | A merged PR closes the issue, but the issue is still open |
| 👀 | **LOOK** | Someone said they'd take it, in a comment or in the issue form (`[x] I'm willing to submit a PR`) |
| 👀 | **LOOK** | A comment says it's already fixed on main |
| 👀 | **LOOK** | A label like `wontfix`, `duplicate`, `invalid` or `blocked` |
| 👀 | **LOOK** | An open PR has one of your `-k` words in its title |
| 👀 | **LOOK** | A rule that can get your PR closed: an issue gate (an `accepted` label, or getting assigned first), a ban on AI-written code, a line asking you to disclose AI use, or a project that takes no outside PRs |
| 📝 | **NOTE** | Your own PRs for the issue |
| 📝 | **NOTE** | Closed PRs that were not merged, so you can read why before you redo them |
| 📝 | **NOTE** | PRs in other repositories (forks) that mention the issue |
| 📝 | **NOTE** | Other lines about AI tools, a CLA or a DCO sign-off, and links to a contribution guide on another page (like a wiki) |

The rules come from `CONTRIBUTING.md` (or `.rst`), the PR template, `AGENTS.md` and `AI_POLICY.md`.

## 🤖 For AI agents

Coding agents open a lot of duplicate PRs, and they rarely read `CONTRIBUTING.md` first. calldibs gives them both answers in one command.

### Install the skill

The repo ships an [agent skill](skills/calldibs/SKILL.md) that tells an agent when to run calldibs and what to do with each result. Install it with the [`skills`](https://github.com/vercel-labs/skills) CLI:

```bash
npx skills add bercedev/calldibs            # this project
npx skills add bercedev/calldibs -g         # all your projects
npx skills add bercedev/calldibs -a claude-code   # one agent only
```

It works with Claude Code, Codex, Cursor, OpenCode and the other agents the `skills` CLI supports. You can also copy `skills/calldibs` into your agent's skills folder by hand, for example `~/.claude/skills/`.

### JSON output

```json
{
  "target": "acme/widget#412",
  "title": "Crash when the config file is empty",
  "verdict": "stop",
  "summary": "someone is already on this, or it is not open for work",
  "findings": [
    {"level": "STOP", "message": "open PR #418 by alice: fix(config): handle an empty file"},
    {"level": "NOTE", "message": "CLA: CONTRIBUTING.md:31: You need to sign a Contributor License Agreement (CLA) before we can accept your PR."}
  ]
}
```

On an error it prints `{"error": "..."}` and exits with `2`.

## 🤝 Use it kindly

calldibs is here to save maintainers from duplicate work, not to make more of it.

- 🙅 **Don't farm contributions.** Opening PRs in bulk, or to grow a contribution graph, wastes maintainers' time even when no two PRs are the same.
- 🎯 **Pick issues you'll see through.** Reply to reviews, change what they ask, and close the PR yourself if you can't finish it.
- 📜 **`clear` is not an invitation.** It means nobody else is on the issue, not that the project wants your PR. Read the rules, and if a project bans AI-written code, don't send any.
- 🙋 **Claim first when the project asks.** Comment on the issue and wait for a maintainer before you start.
- 🤖 **Keep a human in the loop.** If an agent runs calldibs, it works on one issue at a time, and a person reviews every PR before it's opened.

`calldibs find` lists at most 30 issues on purpose. It's for picking one, not for feeding a pipeline.

## ⚠️ Limits

- 📜 The rule lines come from keyword matching. They point you to the right part of the file; read the file itself before you decide.
- 🗣️ Claim detection only knows English phrases like "I'd like to work on this", "can I take this" and `/assign`.
- 📄 It reads the newest 100 comments and the newest 100 linked PRs. That covers nearly every issue.
- 🤖 It does not look at CLA bots or required checks on recent PRs.

## 🛠️ Development

```bash
uv sync
uv run ruff format src tests
uv run ruff check src tests
uv run python -m unittest discover -s tests
```

The code is one module, [`src/calldibs/cli.py`](src/calldibs/cli.py), with no runtime dependencies. [`AGENTS.md`](AGENTS.md) has the notes for coding agents working on this repo.

## 📄 License

[MIT](LICENSE)
