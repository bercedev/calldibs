---
name: calldibs
description: Check whether anyone is already working on a GitHub issue, and what the project asks from contributors (CLA, DCO, AI policy, issue gates), before you start a fix or open a pull request. Also finds open issues nobody is working on when the user asks for something to fix. Use it whenever you are about to work on a GitHub issue in someone else's repository.
---

# calldibs

Run it before you write code for a GitHub issue, and again right before you open the pull request, since someone may have opened one in the meantime:

```
calldibs owner/repo#123 --json
```

If `calldibs` is not installed, run it through uv with `uvx --from git+https://github.com/bercedev/calldibs calldibs owner/repo#123 --json`, or ask the user to install it.

It needs a GitHub token in `GH_TOKEN` or `GITHUB_TOKEN`, or a logged-in `gh`.

## Reading the result

The JSON has a `verdict` and a list of `findings`, each with a `level` and a `message`. The exit status is 0 for clear, 1 for stop or look, and 2 for an error (the JSON then has only an `error` key).

- `stop`: do not start. Someone has an open PR that closes the issue, the issue is assigned to someone else, or the issue or repository is closed. Tell the user what you found.
- `look`: open each LOOK item and read it. Tell the user what you found and let them decide. A LOOK can be:
  - a PR that mentions the issue, a comment where someone said they would take it, or one that says it is already fixed on main;
  - an `issue gate` rule: the issue needs a label (like `accepted`) or an assignment before a PR is accepted. Do not open a PR until the gate is met;
  - an `AI policy` line that bans AI-written contributions. Never open a PR against a ban;
  - an `AI policy` line that asks you to disclose AI use. Tell the user, and disclose it where and how the project asks;
  - a `no PRs` line: the project does not take outside pull requests. Do not open one.
- `clear`: nobody else is on the issue. It does not mean the project wants a PR. Still follow the NOTE lines.

NOTE lines:

- `AI policy`: read that part of the file.
- `linked guide`: the project keeps its rules on another page, like a wiki. Open the link and read it before you start.
- `CLA`: the user has to sign it themselves. Do not sign it for them.
- `DCO`: commits need a `Signed-off-by` line (`git commit -s`).
- A closed PR that was not merged: read why it was closed before you redo the same fix.

## Options

- `-k WORD` (repeatable): also flag open PRs with that word in the title. Use words from the bug, like a function name or an error message, to catch PRs that do not link the issue.
- `--ignore PR` (repeatable): skip a PR. Use it only after you read that PR and found it unrelated, and tell the user you did.
- `calldibs owner/repo --json` prints only the repository's contribution rules.

## Finding an issue to work on

Only when the user asks for something to work on:

```
calldibs find --label "good first issue" --language python --json
```

Options: search words, `--repo`, `--label`, `--language`, `--min-stars`, `--days` (30), `--max-comments` (3), `--limit` (10, at most 30).

Show the list to the user and let them pick one. Then run the check above on that issue before you start.

## Rules

- Work on one issue at a time, and only on issues the user chose.
- Never open pull requests in bulk, and never open one to grow a contribution count.
- The user reviews every pull request before it is opened.
