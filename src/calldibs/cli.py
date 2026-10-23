"""Command line entry: one GraphQL request, then a list of findings and an exit code."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from calldibs import __version__

API = "https://api.github.com/graphql"

STOP, LOOK, NOTE = "STOP", "LOOK", "NOTE"
ORDER = {STOP: 0, LOOK: 1, NOTE: 2}

# Where projects usually write down their contribution rules.
RULE_FILES = (
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE.md",
    "AGENTS.md",
    "AI_POLICY.md",
)
# A line counts for a rule when every pattern of that rule matches it.
RULES = {
    "AI policy": (
        re.compile(r"(?-i:\bAI\b)|\bLLMs?\b|\bgenerative\b|\bcopilot\b|\bchatgpt\b|\bagentic\b|human[- ]written", re.I),
        # rule language, so "an AI agent framework" in a project description does not count
        re.compile(
            r"\b(?:disclos\w*|must|required?|do not|don't|never|not accept\w*|reject\w*|ban\w*|prohibit\w*"
            r"|forbid\w*|allow\w*|limit\w*|polic(?:y|ies)|generated|assisted|rule|use of)\b",
            re.I,
        ),
    ),
    "CLA": (re.compile(r"(?-i:\bCLA\b)|contributor license agreement", re.I),),
    "DCO": (re.compile(r"(?-i:\bDCO\b)|signed-off-by|developer certificate of origin", re.I),),
    "issue gate": (
        re.compile(
            r"\bassigned\b.{0,40}\bbefore\b|\bbefore\b.{0,40}\bassigned\b|\bclaim (?:an|the|this) issue\b"
            r"|\bmust (?:carry|have|be labell?ed)\b.{0,30}\blabel|\bclosed automatically\b|\bautomatically closed\b",
            re.I,
        ),
    ),
}
LINES_PER_RULE = 2

# Someone saying they are taking the issue, in a comment or in the issue form.
CLAIM = re.compile(
    r"\b(?:i[’']?m|i am|i[’']?ll|i will|i[’']?d like to|i would like to|can i|could i|may i|let me)\b[^.\n]{0,30}?"
    r"\b(?:work(?:ing)? on|tak(?:e|ing)|pick(?:ing)? (?:it|this) up|fix(?:ing)?|handl(?:e|ing)|submit(?:ting)?"
    r"|open(?:ing)? a pr)\b"
    r"|^\s*/(?:assign|take|claim)\b"
    r"|\bassign (?:it|this)(?: issue)? to me\b"
    r"|\[x\][^\n]{0,80}\b(?:willing|happy|plan(?:ning)?) to\b",
    re.I | re.M,
)
UNCHECKED = re.compile(r"^\s*[-*] \[ \].*$", re.M)
BAD_LABELS = ("wontfix", "won't fix", "invalid", "duplicate", "not planned", "blocked", "on hold")

TARGET = re.compile(
    r"^(?:https?://github\.com/)?(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?"
    r"(?:(?:#|/issues/)(?P<num>\d+))?/?$"
)

PR_FIELDS = "number title state isDraft url author { login } repository { nameWithOwner }"


class Error(Exception):
    pass


def build_query(with_issue: bool, keywords: int) -> str:
    params = ["$owner: String!", "$name: String!"]
    parts = []
    if with_issue:
        params += ["$num: Int!", "$mentions: String!"]
        # shortcut: one page of comments and links (last 100 / first 100), enough for nearly every issue
        parts.append(
            f"""
    issue(number: $num) {{
      title state stateReason body
      author {{ login __typename }}
      assignees(first: 10) {{ nodes {{ login }} }}
      labels(first: 30) {{ nodes {{ name }} }}
      comments(last: 100) {{ nodes {{ author {{ login __typename }} createdAt body }} }}
      timelineItems(first: 100, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {{
        nodes {{
          ... on CrossReferencedEvent {{ willCloseTarget source {{ ... on PullRequest {{ {PR_FIELDS} }} }} }}
          ... on ConnectedEvent {{ subject {{ ... on PullRequest {{ {PR_FIELDS} }} }} }}
        }}
      }}
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) {{ nodes {{ {PR_FIELDS} }} }}
    }}"""
        )
    files = "".join(
        f'\n    f{i}: object(expression: "HEAD:{path}") {{ ... on Blob {{ text }} }}' for i, path in enumerate(RULE_FILES)
    )
    searches = ""
    if with_issue:
        searches += f"\n  mentions: search(query: $mentions, type: ISSUE, first: 30) {{ nodes {{ ... on PullRequest {{ {PR_FIELDS} body }} }} }}"
    for i in range(keywords):
        params.append(f"$kw{i}: String!")
        searches += f"\n  kw{i}: search(query: $kw{i}, type: ISSUE, first: 10) {{ nodes {{ ... on PullRequest {{ {PR_FIELDS} }} }} }}"
    return (
        f"query({', '.join(params)}) {{\n  viewer {{ login }}\n"
        f"  repository(owner: $owner, name: $name) {{\n    nameWithOwner isArchived{files}{''.join(parts)}\n  }}{searches}\n}}"
    )


def get_token() -> str:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if shutil.which("gh"):
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    raise Error("no GitHub token: set GH_TOKEN or run `gh auth login`")


def graphql(query: str, variables: dict, token: str) -> dict:
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"calldibs/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as e:
        raise Error(f"GitHub API returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise Error(f"cannot reach GitHub: {e.reason}") from None
    # NOT_FOUND (missing repository, or a PR number instead of an issue) is reported by the caller.
    for err in reply.get("errors", []):
        if err.get("type") != "NOT_FOUND":
            raise Error(err.get("message", "GraphQL error"))
    return reply.get("data") or {}


def one_line(text: str, width: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def line_at(text: str, pos: int) -> str:
    end = text.find("\n", pos)
    return text[text.rfind("\n", 0, pos) + 1 : None if end == -1 else end]


def claim_line(text: str) -> str | None:
    text = UNCHECKED.sub("", text)  # an unticked "I'm willing to submit a PR" box is not a claim
    match = CLAIM.search(text)
    return line_at(text, match.start()) if match else None


def mention_pattern(owner: str, name: str, num: int) -> str:
    repo = re.escape(f"{owner}/{name}")
    return (
        rf"(?:(?<![\w/#-])|(?<![\w.-]){repo})#{num}(?!\d)|\bGH-{num}(?!\d)"
        rf"|(?:https?://)?github\.com/{repo}/issues/{num}(?!\d)"
    )


def rule_findings(files: dict[str, str]) -> list[tuple[str, str]]:
    found = []
    for rule, patterns in RULES.items():
        hits = [
            f"{path}:{lineno}: {one_line(line, 110)}"
            for path, text in files.items()
            for lineno, line in enumerate(text.splitlines(), 1)
            if all(p.search(line) for p in patterns)
        ]
        found += [(NOTE, f"{rule}: {hit}") for hit in hits[:LINES_PER_RULE]]
        if (more := len(hits) - LINES_PER_RULE) > 0:
            found.append((NOTE, f"{rule}: {more} more line{'s' * (more != 1)}"))
    return found


def linked_prs(data: dict, owner: str, name: str, num: int) -> dict[tuple[str, int], dict]:
    """PRs that point at the issue, keyed by (repo, number), each marked with whether it would close it."""
    issue = data["repository"]["issue"]
    prs: dict[tuple[str, int], dict] = {}

    def add(pr: dict | None, closes: bool) -> None:
        if not pr or "number" not in pr:
            return
        key = (pr["repository"]["nameWithOwner"].lower(), pr["number"])
        prs[key] = dict(pr, closes=closes or prs.get(key, {}).get("closes", False))

    for pr in issue["closedByPullRequestsReferences"]["nodes"]:
        add(pr, True)
    for event in issue["timelineItems"]["nodes"]:
        if "source" in event:
            add(event["source"], bool(event.get("willCloseTarget")))
        elif "subject" in event:
            add(event["subject"], True)
    mention = mention_pattern(owner, name, num)
    closing = re.compile(rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+(?:{mention})", re.I)
    for pr in (data.get("mentions") or {}).get("nodes", []):
        text = f"{(pr or {}).get('title', '')}\n{(pr or {}).get('body') or ''}"
        if re.search(mention, text, re.I):
            add(pr, bool(closing.search(text)))
    return prs


def pr_findings(prs: dict, here: str, viewer: str, ignore: set[int]) -> list[tuple[str, str]]:
    found = []
    for (repo, number), pr in sorted(prs.items()):
        if repo == here and number in ignore:
            continue
        who = (pr.get("author") or {}).get("login", "ghost")
        ref = f"#{number}" if repo == here else f"{pr['repository']['nameWithOwner']}#{number}"
        desc = f"{ref} by {who}: {one_line(pr['title'], 70)}"
        state = pr["state"]
        if who == viewer:
            found.append((NOTE, f"your PR {ref} is {state.lower()}"))
        elif repo != here:
            found.append((NOTE, f"a PR in another repository mentions this issue: {desc}"))
        elif state == "OPEN":
            draft = "draft " if pr.get("isDraft") else ""
            if pr["closes"]:
                found.append((STOP, f"open {draft}PR {desc}"))
            else:
                found.append((LOOK, f"open {draft}PR mentions this issue: {desc}"))
        elif state == "MERGED":
            if pr["closes"]:
                found.append((LOOK, f"merged PR {desc}; the issue may already be fixed"))
            else:
                found.append((NOTE, f"merged PR mentions this issue: {desc}"))
        else:
            found.append((NOTE, f"closed PR {desc}; it was not merged, read why before redoing it"))
    return found


def claim_findings(issue: dict, viewer: str) -> list[tuple[str, str]]:
    found, seen = [], set()
    posts = [{"author": issue["author"], "createdAt": None, "body": issue["body"]}] + issue["comments"]["nodes"]
    for post in reversed(posts):  # newest first, one line per person
        author = post.get("author") or {}
        who = author.get("login")
        if not who or who == viewer or who in seen or author.get("__typename") == "Bot":
            continue
        line = claim_line(post.get("body") or "")
        if line is not None:
            seen.add(who)
            when = f"on {post['createdAt'][:10]}" if post["createdAt"] else "in the issue"
            found.append((LOOK, f'{who} said they would take it {when}: "{one_line(line, 90)}"'))
    return found


def assess(data: dict, owner: str, name: str, num: int, ignore=frozenset(), keywords=()) -> list[tuple[str, str]]:
    viewer = data["viewer"]["login"]
    repo = data["repository"]
    issue = repo["issue"]
    here = repo["nameWithOwner"].lower()
    found = []
    if repo["isArchived"]:
        found.append((STOP, "the repository is archived"))
    if issue["state"] != "OPEN":
        reason = (issue.get("stateReason") or "closed").lower().replace("_", " ")
        found.append((STOP, f"the issue is closed ({reason})"))
    assignees = [a["login"] for a in issue["assignees"]["nodes"]]
    others = [a for a in assignees if a != viewer]
    if others:
        found.append((STOP, f"assigned to {', '.join(others)}"))
    elif assignees:
        found.append((NOTE, "assigned to you"))
    for label in issue["labels"]["nodes"]:
        if any(bad in label["name"].lower() for bad in BAD_LABELS):
            found.append((LOOK, f"labeled {label['name']!r}"))

    prs = linked_prs(data, owner, name, num)
    found += pr_findings(prs, here, viewer, set(ignore))
    reported = {number for repo_, number in prs if repo_ == here} | set(ignore)
    for i, keyword in enumerate(keywords):
        for pr in (data.get(f"kw{i}") or {}).get("nodes", []):
            if not pr or pr["number"] in reported or (pr.get("author") or {}).get("login") == viewer:
                continue
            reported.add(pr["number"])
            who = (pr.get("author") or {}).get("login", "ghost")
            found.append((LOOK, f'open PR with "{keyword}" in the title: #{pr["number"]} by {who}: {one_line(pr["title"], 70)}'))
    found += claim_findings(issue, viewer)
    return found


def report(header: str, found: list[tuple[str, str]], out=None) -> int:
    out = out or sys.stdout
    print(header, file=out)
    for level, text in sorted(found, key=lambda f: ORDER[f[0]]):
        print(f"  {level}  {text}", file=out)
    levels = {level for level, _ in found}
    if STOP in levels:
        verdict = "stop: someone is already on this, or it is not open for work"
    elif LOOK in levels:
        verdict = "look first: read the items above before you start"
    else:
        verdict = "clear: nobody seems to be working on this"
    print(verdict, file=out)
    return 1 if levels & {STOP, LOOK} else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calldibs",
        description="Check that nobody is already working on a GitHub issue before you start a fix.",
        epilog="Exit status: 0 clear, 1 something to stop or look at, 2 error.",
    )
    parser.add_argument(
        "target", help="owner/repo#123 or an issue URL; owner/repo alone prints the repository's contribution rules"
    )
    parser.add_argument(
        "-k", "--keyword", action="append", default=[], help="also flag open PRs with this word in the title (repeatable)"
    )
    parser.add_argument(
        "--ignore", action="append", type=int, default=[], metavar="PR",
        help="skip this PR number after you checked it by hand (repeatable)",
    )
    parser.add_argument("--version", action="version", version=f"calldibs {__version__}")
    args = parser.parse_args(argv)

    m = TARGET.match(args.target.strip())
    if not m:
        parser.error(f"cannot read {args.target!r}: use owner/repo#123 or an issue URL")
    owner, name, num = m["owner"], m["name"], m["num"]
    if num is None and args.keyword:
        parser.error("--keyword needs an issue")

    variables: dict = {"owner": owner, "name": name}
    if num:
        variables |= {"num": int(num), "mentions": f"repo:{owner}/{name} is:pr {num}"}
    for i, keyword in enumerate(args.keyword):
        variables[f"kw{i}"] = f'repo:{owner}/{name} is:pr is:open in:title "{keyword.replace(chr(34), "")}"'
    try:
        data = graphql(build_query(num is not None, len(args.keyword)), variables, get_token())
        repo = data.get("repository")
        if not repo:
            raise Error(f"{owner}/{name}: repository not found")
        if num and not repo.get("issue"):
            raise Error(f"{owner}/{name}#{num} is not an issue (a pull request, or it does not exist)")
    except Error as e:
        print(f"calldibs: {e}", file=sys.stderr)
        return 2

    files = {
        path: obj["text"] for i, path in enumerate(RULE_FILES) if (obj := repo.get(f"f{i}")) and obj.get("text")
    }
    rules = rule_findings(files)
    if num is None:
        print(f"{repo['nameWithOwner']} contribution rules")
        for _, text in rules:
            print(f"  {text}")
        if not rules:
            print(f"  none found in {', '.join(RULE_FILES)}")
        return 0
    found = assess(data, owner, name, int(num), args.ignore, args.keyword) + rules
    return report(f"{repo['nameWithOwner']}#{num}  {one_line(repo['issue']['title'], 80)}", found)
