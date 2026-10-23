# calldibs

Check that nobody is already working on a GitHub issue before you start a fix.

Before I pick up an issue I want to know a few things. Is there already a PR for it? Did someone say they'd take it? Is it assigned? And what does the project ask from contributors: a CLA, a DCO sign-off, an AI policy, an issue that has to be accepted first? `calldibs` answers all of that with one GraphQL request.

```
$ calldibs acme/widget#412
acme/widget#412  Crash when the config file is empty
  STOP  open PR #418 by alice: fix(config): handle an empty file
  LOOK  bob said they would take it on 2026-10-20: "I'd like to work on this"
  NOTE  closed PR #405 by carol: guard empty config; it was not merged, read why before redoing it
  NOTE  CLA: CONTRIBUTING.md:31: You need to sign a Contributor License Agreement (CLA) before we can accept your PR.
  NOTE  issue gate: CONTRIBUTING.md:38: The issue must carry the `accepted` label before you open a PR.
stop: someone is already on this, or it is not open for work
```

## Install

With uv:

```
uv tool install git+https://github.com/bercedev/calldibs
```

With pip or pipx:

```
pipx install git+https://github.com/bercedev/calldibs
pip install git+https://github.com/bercedev/calldibs
```

To run it once without installing: `uvx --from git+https://github.com/bercedev/calldibs calldibs owner/repo#123`.

It needs Python 3.10 or newer and has no dependencies.

It reads a token from `GH_TOKEN` or `GITHUB_TOKEN`. If neither is set, it asks `gh auth token`. The token only needs read access to public repositories.

## Usage

```
calldibs owner/repo#123           # check an issue
calldibs https://github.com/owner/repo/issues/123
calldibs owner/repo#123 -k crash  # also flag open PRs with "crash" in the title
calldibs owner/repo#123 --ignore 456   # skip PR #456 after you checked it by hand
calldibs owner/repo               # only print the repository's contribution rules
calldibs owner/repo#123 --json    # JSON for scripts and agents
```

Exit status is 0 when the issue is clear, 1 when there is something to stop at or look at, and 2 on errors. So you can put it in front of a push:

```
calldibs owner/repo#123 -k crash && git push && gh pr create
```

## What it checks

**STOP**
- the issue is closed, or the repository is archived
- the issue is assigned to someone else
- an open PR would close the issue (`Fixes #123`, or linked in the sidebar)

**LOOK**
- an open PR mentions the issue without closing it
- a merged PR closes the issue, but the issue is still open
- someone said they'd take it, in a comment or in the issue form (`[x] I'm willing to submit a PR`)
- a label like `wontfix`, `duplicate`, `invalid` or `blocked`
- an open PR has one of your `-k` words in its title

**NOTE**
- your own PRs for the issue
- closed PRs that were not merged, so you can read why before you redo them
- PRs in other repositories (forks) that mention the issue
- lines about AI tools, a CLA, a DCO sign-off, or an issue gate in `CONTRIBUTING.md`, the PR template, `AGENTS.md` or `AI_POLICY.md`

## AI agents

Coding agents open a lot of duplicate PRs, and they rarely read `CONTRIBUTING.md` first. calldibs gives them both answers in one command.

`--json` prints the verdict and the findings:

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

On an error it prints `{"error": "..."}` and exits with 2.

[`skills/calldibs/SKILL.md`](skills/calldibs/SKILL.md) tells an agent when to run calldibs and what to do with each result. Agents that read `SKILL.md` files can use it as is. For Claude Code, copy the `skills/calldibs` folder into `~/.claude/skills/`.

## Limits

- The rule lines come from keyword matching. They point you to the right part of the file; read the file itself before you decide.
- Claim detection only knows English phrases like "I'd like to work on this", "can I take this" and `/assign`.
- It reads the last 100 comments and the first 100 linked PRs. That covers nearly every issue.
- It does not look at CLA bots or required checks on recent PRs.

## Development

```
uv sync
uv run ruff format src tests
uv run ruff check src tests
uv run python -m unittest discover -s tests
```

## License

MIT
