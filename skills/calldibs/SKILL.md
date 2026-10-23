---
name: calldibs
description: Check whether anyone is already working on a GitHub issue, and what the project asks from contributors (CLA, DCO, AI policy, issue gates), before you start a fix or open a pull request. Use it whenever you are about to work on a GitHub issue in someone else's repository.
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
- `look`: open each LOOK item and read it: the PR, or the comment where someone said they would take the issue. Tell the user what you found and let them decide.
- `clear`: you can start. Still follow the NOTE lines below.

NOTE lines:

- `AI policy`: read that part of the file. Some projects ban AI-written contributions and others want a disclosure. Never open a PR against a ban.
- `CLA`: the user has to sign it themselves. Do not sign it for them.
- `DCO`: commits need a `Signed-off-by` line (`git commit -s`).
- `issue gate`: the issue may need a label or an assignment before a PR is accepted.
- A closed PR that was not merged: read why it was closed before you redo the same fix.

## Options

- `-k WORD` (repeatable): also flag open PRs with that word in the title. Use words from the bug, like a function name or an error message, to catch PRs that do not link the issue.
- `--ignore PR` (repeatable): skip a PR. Use it only after you read that PR and found it unrelated, and tell the user you did.
- `calldibs owner/repo --json` prints only the repository's contribution rules.
