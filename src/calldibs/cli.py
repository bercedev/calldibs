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
from datetime import date, timedelta

from calldibs import __version__

API = "https://api.github.com/graphql"

STOP, LOOK, NOTE = "STOP", "LOOK", "NOTE"
ORDER = {STOP: 0, LOOK: 1, NOTE: 2}
VERDICTS = {
    "stop": "someone is already on this, or it is not open for work",
    "look": "read the items above before you start",
    "clear": "nobody seems to be working on this",
}

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
# Wording that turns AI contributions away; "we do not ban AI tools" is not a ban.
AI_BAN = re.compile(
    r"\b(?:not accept\w*|reject\w*|(?<!not )(?<!n't )(?:ban(?:s|ned)?|prohibit\w*|forbid\w*)"
    r"|not (?:allowed|permitted)|will be closed|must not)\b",
    re.I,
)
GUIDE_LINK = re.compile(r"https?://[^\s)>\]]*contribut[^\s)>\]]*", re.I)
# A line counts for a rule when every pattern of that rule matches it.
RULES = {
    "AI policy": (
        re.compile(
            r"(?-i:\bAI\b)|\bLLMs?\b|\bgenerative\b|\bcopilot\b|\bchatgpt\b|\bagentic\b|human[- ]written"
            r"|\bagent[- ]driven\b|\b(?:autonomous|coding) agents?\b"
            r"|\bagents? (?:are|is) (?:strictly )?(?:forbidden|prohibited|banned|not (?:allowed|permitted))\b",
            re.I,
        ),
        # rule language, so "an AI agent framework" in a project description does not count
        re.compile(
            r"\b(?:disclos\w*|must|required?|do not|don't|never|ban\w*|prohibit\w*|forbid\w*|allow\w*|limit\w*"
            r"|polic(?:y|ies)|generated|assisted|rule|use of)\b|" + AI_BAN.pattern,
            re.I,
        ),
    ),
    "CLA": (re.compile(r"(?-i:\bCLA\b)|contributor license agreement", re.I),),
    "DCO": (re.compile(r"(?-i:\bDCO\b)|signed-off-by|developer certificate of origin", re.I),),
    "issue gate": (
        re.compile(
            # you have to be assigned first: "before a reviewer is assigned" is not about you
            r"\b(?:get|be|wait to be|until you are) assigned\b.{0,60}\b(?:before|first)\b"
            r"|\bbefore\b.{0,60}\b(?:get|be) assigned\b|\bassigned to you\b|\bclaim (?:an|the|this) issue\b"
            r"|\bmust (?:carry|have|be labell?ed)\b.{0,30}\blabel"
            # PRs closed by a bot; stale issues closed after N days are not a gate
            # "never closed automatically" says the opposite
            r"|\b(?:pull requests?|PRs?)\b[^.]{0,80}(?<!never )(?<!not )(?<!n't )"
            r"\b(?:closed automatically|automatically closed)\b"
            r"|\bautomatically close[sd]? (?:pull requests?|PRs?)\b",
            re.I,
        ),
    ),
    # the rules live on another page, like VS Code's wiki
    "linked guide": (GUIDE_LINK,),
}
LINES_PER_RULE = 2
DISCLOSE = re.compile(r"\bdisclos\w*", re.I)
# "AI PRs are accepted only if a maintainer replied on the issue" is a gate
CONDITION = re.compile(r"\bonly (?:if|when|after)\b|\bunless\b", re.I)

# Someone saying they are taking the issue, in a comment or in the issue form.
CLAIM = re.compile(
    r"\b(?:i[\u2019']?m|i am|i[\u2019']?ll|i will|i[\u2019']?d like to|i would like to|can i|could i|may i|let me)\b"
    r"[^.\n]{0,30}?"
    # the verb needs the issue or a fix as its object: "I'm working on a Next.js app" is not a claim
    r"\b(?:(?:work(?:ing)? on|tak(?:e|ing)|fix(?:ing)?|handl(?:e|ing)|submit(?:ting)?|open(?:ing)?|send(?:ing)?)"
    r"\s+(?:this|it|the issue|this issue|this one|a fix|the fix|a pr|a pull request)"
    r"|pick(?:ing)? (?:it|this(?: one)?) up)\b"
    r"|^\s*/(?:assign|take|claim)\b"
    r"|\bassign (?:it|this)(?: issue)? to me\b"
    r"|\[x\][^\n]{0,80}\b(?:willing|happy|plan(?:ning)?) to\b"
    r"|\b(?:my (?:fix|patch|branch)|fix branch)\b[^.\n?]{0,25}\bis ready\b|\bready (?:on|in) my (?:fork|branch)\b"
    r"|\bhappy to (?:send|open|submit|make) (?:one|a pr|a pull request|a fix|the fix)\b"
    r"|\bi(?:[\u2019']ve| have) (?:pushed|created|made) (?:a |my )?(?:\w+ )?(?:branch|commit|fix|patch)\b"
    r"|\binterested in\b[^.\n]{0,60}\b(?:work(?:ing)? on|fix(?:ing)?|tak(?:e|ing)) (?:this|it|a fix|the fix)\b",
    re.I | re.M,
)
# GitHub can limit PRs to collaborators; people then post a branch link in the issue instead.
COLLABORATORS_ONLY = re.compile(
    r"\bnot a collaborator\b|\blimits? pull requests to collaborators\b|\bPRs are (?:limited|restricted) to\b", re.I
)
# Someone in the comments saying the bug is gone on the main branch.
FIXED = re.compile(r"\balready (?:been )?fixed\b|\bfixed (?:on|in) (?:main|master)\b|\bno longer reproduces?\b", re.I)
UNCHECKED = re.compile(r"^\s*[-*] \[ \].*$", re.M)
BAD_LABEL = re.compile(r"\b(?:won'?t[ -]?fix|invalid|duplicate|not[ -]planned|blocked|on[ -]hold)\b", re.I)
# Some projects (VS Code) assign every issue to the area owner and invite PRs with this label.
HELP_WANTED = re.compile(r"\bhelp[ -]wanted\b", re.I)

# Also takes links copied from notifications, with a #issuecomment-… fragment or a query string.
TARGET = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/)?(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?"
    r"(?:(?:#|/issues/)(?P<num>\d+))?/?(?:[#?].*)?$"
)

PR_FIELDS = "number title state isDraft url author { login } repository { nameWithOwner }"
# `calldibs find`: a short list to pick from, not a feed for opening PRs in bulk.
# shortcut: claims older than an issue's last 30 comments are missed here; the issue check reads 100
MAX_FIND = 30
FIND_PAGES = 3
REPOS_PER_SEARCH = 10
REPO_QUERY = """query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 20) { nodes { ... on Repository { nameWithOwner } } }
}"""
FIND_QUERY = """query($q: String!, $after: String) {
  viewer { login }
  search(query: $q, type: ISSUE, first: 30, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number title url createdAt body
        author { login __typename }
        repository { nameWithOwner stargazerCount }
        assignees(first: 5) { nodes { login } }
        labels(first: 10) { nodes { name } }
        comments(last: 30) { totalCount nodes { author { login __typename } createdAt body } }
        timelineItems(last: 20, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
          nodes {
            ... on CrossReferencedEvent { source { ... on PullRequest { state } } }
            ... on ConnectedEvent { subject { ... on PullRequest { state } } }
          }
        }
      }
    }
  }
}"""
HTTP_HINTS = {
    401: ": the token is invalid or expired",
    403: ": rate limited, or the token has no access",
    429: ": rate limited, try again later",
}


class Error(Exception):
    pass


def build_query(with_issue: bool, keywords: int) -> str:
    params = ["$owner: String!", "$name: String!"]
    parts = []
    if with_issue:
        params += ["$num: Int!", "$mentions: String!"]
        # shortcut: one page each of the newest comments and links, enough for nearly every issue
        parts.append(
            f"""
    issue(number: $num) {{
      title state stateReason body
      author {{ login __typename }}
      assignees(first: 10) {{ nodes {{ login }} }}
      labels(first: 30) {{ nodes {{ name }} }}
      comments(last: 100) {{ nodes {{ author {{ login __typename }} createdAt body }} }}
      timelineItems(last: 100, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {{
        nodes {{
          ... on CrossReferencedEvent {{ willCloseTarget source {{ ... on PullRequest {{ {PR_FIELDS} }} }} }}
          ... on ConnectedEvent {{ subject {{ ... on PullRequest {{ {PR_FIELDS} }} }} }}
        }}
      }}
      closedByPullRequestsReferences(last: 20, includeClosedPrs: true) {{ nodes {{ {PR_FIELDS} }} }}
    }}"""
        )
    files = "".join(
        f'\n    f{i}: object(expression: "HEAD:{path}") {{ ... on Blob {{ text }} }}'
        for i, path in enumerate(RULE_FILES)
    )
    searches = [("mentions", 30, " body")] if with_issue else []
    for i in range(keywords):
        params.append(f"$kw{i}: String!")
        searches.append((f"kw{i}", 10, ""))
    search_fields = "".join(
        f"\n  {alias}: search(query: ${alias}, type: ISSUE, first: {first}) "
        f"{{ nodes {{ ... on PullRequest {{ {PR_FIELDS}{extra} }} }} }}"
        for alias, first, extra in searches
    )
    return (
        f"query({', '.join(params)}) {{\n  viewer {{ login }}\n"
        f"  repository(owner: $owner, name: $name) {{\n    nameWithOwner isArchived{files}{''.join(parts)}\n  }}"
        f"{search_fields}\n}}"
    )


def get_token() -> str:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise Error("`gh auth token` did not answer in 10 seconds") from None
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
        hint = HTTP_HINTS.get(e.code, "")
        raise Error(f"GitHub API returned HTTP {e.code}{hint}") from None
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


def rule_level(rule: str, line: str) -> str:
    # an issue gate, an AI ban or a required AI disclosure decides how (or whether) a PR goes in
    if rule == "issue gate" or (rule == "AI policy" and any(p.search(line) for p in (AI_BAN, DISCLOSE, CONDITION))):
        return LOOK
    return NOTE


def rule_text(rule: str, line: str) -> str:
    # a guide line is often long; the link is what to open
    if rule == "linked guide" and (link := GUIDE_LINK.search(line)):
        return link.group()
    return one_line(line, 110)


def rule_findings(files: dict[str, str]) -> list[tuple[str, str]]:
    found = []
    for rule, patterns in RULES.items():
        hits = [
            (rule_level(rule, line), f"{rule}: {path}:{lineno}: {rule_text(rule, line)}")
            for path, text in files.items()
            for lineno, line in enumerate(text.splitlines(), 1)
            if all(p.search(line) for p in patterns)
        ]
        hits.sort(key=lambda hit: ORDER[hit[0]])  # keep a LOOK line from falling past the cut
        found += hits[:LINES_PER_RULE]
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
    # a dependabot changelog links "#123" to another repository's PR, in Markdown or HTML: not this issue
    other = rf"https?://(?:redirect\.)?github\.com/(?!{re.escape(f'{owner}/{name}/')})"
    elsewhere = re.compile(rf"\[[^\]]*\]\({other}[^)]*\)|<a\s[^>]*href=\"{other}[^\"]*\"[^>]*>.*?</a>", re.I | re.S)
    closing = re.compile(rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+(?:{mention})", re.I)
    for pr in (data.get("mentions") or {}).get("nodes", []):
        text = elsewhere.sub("", f"{(pr or {}).get('title', '')}\n{(pr or {}).get('body') or ''}")
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
        body = post.get("body") or ""
        when = f"on {post['createdAt'][:10]}" if post["createdAt"] else "in the issue"
        if (line := claim_line(body)) is not None:
            seen.add(who)
            found.append((LOOK, f'{who} said they would take it {when}: "{one_line(line, 90)}"'))
        if match := COLLABORATORS_ONLY.search(body):
            said = one_line(line_at(body, match.start()), 90)
            found.append((LOOK, f'{who} says only collaborators can open PRs here {when}: "{said}"'))
        elif post["createdAt"] and (match := FIXED.search(body)):  # the issue body may describe an old fix
            seen.add(who)
            said = one_line(line_at(body, match.start()), 90)
            found.append((LOOK, f'{who} says it may already be fixed {when}: "{said}"'))
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
    wanted = [label["name"] for label in issue["labels"]["nodes"] if HELP_WANTED.search(label["name"])]
    if others and wanted:
        note = "the assignee may own the area, ask them"
        found.append((LOOK, f"assigned to {', '.join(others)}, but labeled {wanted[0]!r}: {note}"))
    elif others:
        found.append((STOP, f"assigned to {', '.join(others)}"))
    elif assignees:
        found.append((NOTE, "assigned to you"))
    for label in issue["labels"]["nodes"]:
        if BAD_LABEL.search(label["name"]):
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
            found.append(
                (LOOK, f'open PR with "{keyword}" in the title: #{pr["number"]} by {who}: {one_line(pr["title"], 70)}')
            )
    found += claim_findings(issue, viewer)
    return found


def report(target: str, title: str, found: list[tuple[str, str]], as_json: bool = False, out=None) -> int:
    out = out or sys.stdout
    found = sorted(found, key=lambda f: ORDER[f[0]])
    levels = {level for level, _ in found}
    verdict = "stop" if STOP in levels else "look" if LOOK in levels else "clear"
    if as_json:
        findings = [{"level": level, "message": text} for level, text in found]
        result = {"target": target, "title": title, "verdict": verdict, "summary": VERDICTS[verdict]}
        json.dump(result | {"findings": findings}, out, indent=2, ensure_ascii=False)
        print(file=out)
    else:
        print(f"{target}  {one_line(title, 80)}", file=out)
        for level, text in found:
            print(f"  {level}  {text}", file=out)
        print(f"{'look first' if verdict == 'look' else verdict}: {VERDICTS[verdict]}", file=out)
    return 0 if verdict == "clear" else 1


def count(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"{value} is negative")
    return number


def find_query(
    words: list[str],
    repos: list[str],
    labels: list[str],
    language: str | None,
    days: int,
    max_comments: int,
    today: date | None = None,
) -> str:
    since = (today or date.today()) - timedelta(days=days)
    # with help wanted, an assignee is often the area owner rather than someone at work on it
    assignee = [] if any(HELP_WANTED.search(label) for label in labels) else ["no:assignee"]
    parts = ["is:issue", "is:open", *assignee, "-linked:pr", "archived:false"]
    parts += [f"created:>={since.isoformat()}", f"comments:<={max_comments}", "sort:created-desc"]
    parts += [f"repo:{repo}" for repo in repos]
    parts += [f'label:"{label.replace(chr(34), "")}"' for label in labels]
    if language:
        parts.append(f"language:{language}")
    return " ".join(parts + words)


def repo_query(min_stars: int, language: str | None, labels: list[str], days: int, today: date | None = None) -> str:
    since = (today or date.today()) - timedelta(days=days)
    parts = [f"stars:>={min_stars}", "archived:false", f"pushed:>={since.isoformat()}", "sort:updated"]
    if language:
        parts.append(f"language:{language}")
    wanted = {label.lower() for label in labels}
    if "good first issue" in wanted:
        parts.append("good-first-issues:>0")
    if "help wanted" in wanted:
        parts.append("help-wanted-issues:>0")
    return " ".join(parts)


def open_for_work(issue: dict, viewer: str) -> bool:
    """No blocking label, no open PR pointing at it, and nobody (but you) said they would take it."""
    if any(BAD_LABEL.search(label["name"]) for label in issue["labels"]["nodes"]):
        return False
    for event in issue["timelineItems"]["nodes"]:
        if (event.get("source") or event.get("subject") or {}).get("state") == "OPEN":
            return False
    return not claim_findings(issue, viewer)


def find(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="calldibs find",
        description="List open issues that nobody seems to be working on: no assignee, no open PR, "
        "no blocking label, and nobody said they would take it.",
        epilog="Pick one you will really work on, then run `calldibs owner/repo#123` on it before you start.",
    )
    parser.add_argument("words", nargs="*", help="search words, e.g. crash or memory leak")
    parser.add_argument(
        "--repo", action="append", default=[], metavar="OWNER/REPO", help="only this repository (repeatable)"
    )
    parser.add_argument(
        "--label", action="append", default=[], help='only issues with this label, e.g. "good first issue"'
    )
    parser.add_argument("--language", help="only repositories in this language, e.g. python")
    parser.add_argument(
        "--min-stars", type=count, default=0, metavar="N", help="only repositories with at least N stars"
    )
    parser.add_argument(
        "--days", type=count, default=30, metavar="N", help="only issues opened in the last N days (30)"
    )
    parser.add_argument(
        "--max-comments", type=count, default=3, metavar="N", help="only issues with at most N comments (3)"
    )
    parser.add_argument(
        "--limit", type=int, default=10, metavar="N", help=f"list at most N issues (10, max {MAX_FIND})"
    )
    parser.add_argument("--json", action="store_true", help="print JSON, for scripts and AI agents")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_FIND:
        parser.error(f"--limit must be between 1 and {MAX_FIND}: list the issues you can really work on")
    if any("/" not in repo for repo in args.repo):
        parser.error("--repo takes owner/repo")

    picked, queries = [], []
    try:
        token = get_token()
        repos, chunks = args.repo, [args.repo]
        if args.min_stars and not repos:
            # issue search cannot filter by stars, so first pick active repositories above the bar
            data = graphql(REPO_QUERY, {"q": repo_query(args.min_stars, args.language, args.label, args.days)}, token)
            repos = [node["nameWithOwner"] for node in data["search"]["nodes"] if node]
            chunks = [repos[i : i + REPOS_PER_SEARCH] for i in range(0, len(repos), REPOS_PER_SEARCH)]
        for chunk in chunks:
            queries.append(find_query(args.words, chunk, args.label, args.language, args.days, args.max_comments))
            after = None
            for _ in range(FIND_PAGES):
                data = graphql(FIND_QUERY, {"q": queries[-1], "after": after}, token)
                search = data["search"]
                for issue in search["nodes"]:
                    if (
                        issue
                        and len(picked) < args.limit
                        and issue["repository"]["stargazerCount"] >= args.min_stars
                        and open_for_work(issue, data["viewer"]["login"])
                    ):
                        picked.append(issue)
                if len(picked) >= args.limit or not search["pageInfo"]["hasNextPage"]:
                    break
                after = search["pageInfo"]["endCursor"]
            if len(picked) >= args.limit:
                break
    except Error as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        print(f"calldibs: {e}", file=sys.stderr)
        return 2

    rows = [
        {
            "target": f"{issue['repository']['nameWithOwner']}#{issue['number']}",
            "title": issue["title"],
            "url": issue["url"],
            "stars": issue["repository"]["stargazerCount"],
            "comments": issue["comments"]["totalCount"],
            "created": issue["createdAt"][:10],
            "labels": [label["name"] for label in issue["labels"]["nodes"]],
            "assignees": [a["login"] for a in (issue.get("assignees") or {}).get("nodes", [])],
        }
        for issue in picked
    ]
    if args.json:
        print(json.dumps({"queries": queries, "issues": rows}, indent=2, ensure_ascii=False))
        return 0
    print(f"{len(rows)} open issue{'s' * (len(rows) != 1)} nobody seems to be working on")
    for row in rows:
        labels = f"  [{', '.join(row['labels'])}]" if row["labels"] else ""
        owner = f"  assigned: {', '.join(row['assignees'])}" if row["assignees"] else ""
        title = one_line(row["title"], 70)
        print(f"  {row['stars']:>7}★  {row['target']}  {row['comments']}c  {row['created']}  {title}{labels}{owner}")
    if rows:
        print(f"next: run `calldibs {rows[0]['target']}` on the one you pick, before you start")
    else:
        print("good issues go fast: try a larger --days or --max-comments, fewer labels, or a lower --min-stars")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["find"]:
        return find(argv[1:])
    parser = argparse.ArgumentParser(
        prog="calldibs",
        description="Check that nobody is already working on a GitHub issue before you start a fix.",
        epilog="Exit status: 0 clear, 1 something to stop or look at, 2 error. "
        "To look for issues to work on, see `calldibs find --help`.",
    )
    parser.add_argument(
        "target", help="owner/repo#123 or an issue URL; owner/repo alone prints the repository's contribution rules"
    )
    parser.add_argument(
        "-k",
        "--keyword",
        action="append",
        default=[],
        help="also flag open PRs with this word in the title (repeatable)",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        type=int,
        default=[],
        metavar="PR",
        help="skip this PR number after you checked it by hand (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON, for scripts and AI agents")
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
        # newest first, so short numbers that also match version strings do not push recent PRs out of the page
        variables |= {"num": int(num), "mentions": f"repo:{owner}/{name} is:pr {num} sort:updated-desc"}
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
        if args.json:
            print(json.dumps({"error": str(e)}))
        print(f"calldibs: {e}", file=sys.stderr)
        return 2

    files = {path: obj["text"] for i, path in enumerate(RULE_FILES) if (obj := repo.get(f"f{i}")) and obj.get("text")}
    rules = rule_findings(files)
    if num is None:
        if args.json:
            rule_list = [{"level": level, "message": text} for level, text in rules]
            print(json.dumps({"target": repo["nameWithOwner"], "rules": rule_list}, indent=2, ensure_ascii=False))
            return 0
        print(f"{repo['nameWithOwner']} contribution rules")
        for level, text in rules:
            print(f"  {level}  {text}")
        if not rules:
            print(f"  none found in {', '.join(RULE_FILES)}")
        return 0
    found = assess(data, owner, name, int(num), args.ignore, args.keyword) + rules
    return report(f"{repo['nameWithOwner']}#{num}", repo["issue"]["title"], found, args.json)
