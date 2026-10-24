import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calldibs import cli
from calldibs.cli import LOOK, NOTE, STOP, TARGET, assess, build_query, claim_line, report, rule_findings


def pr(number, author="alice", state="OPEN", repo="o/r", title="Fix it", body=None):
    node = {
        "number": number,
        "title": title,
        "state": state,
        "isDraft": False,
        "url": f"https://github.com/{repo}/pull/{number}",
        "author": {"login": author},
        "repository": {"nameWithOwner": repo},
    }
    if body is not None:
        node["body"] = body
    return node


def data(**issue_overrides):
    extra = issue_overrides.pop("extra", {})
    issue = {
        "title": "Crash on start",
        "state": "OPEN",
        "stateReason": None,
        "body": "It crashes.",
        "author": {"login": "reporter", "__typename": "User"},
        "assignees": {"nodes": []},
        "labels": {"nodes": []},
        "comments": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }
    issue.update(issue_overrides)
    return {
        "viewer": {"login": "me"},
        "repository": {"nameWithOwner": "o/r", "isArchived": False, "issue": issue},
        "mentions": {"nodes": []},
        **extra,
    }


def comment(who, body, kind="User"):
    return {"author": {"login": who, "__typename": kind}, "createdAt": "2026-10-20T10:00:00Z", "body": body}


def levels(found):
    return [level for level, _ in found]


class AssessTest(unittest.TestCase):
    def test_clear_issue(self):
        found = assess(data(), "o", "r", 12)
        self.assertEqual(found, [])
        self.assertEqual(report("o/r#12", "t", found, out=io.StringIO()), 0)

    def test_open_closing_pr_by_someone_else_stops(self):
        found = assess(data(closedByPullRequestsReferences={"nodes": [pr(40)]}), "o", "r", 12)
        self.assertEqual(levels(found), [STOP])
        self.assertEqual(report("o/r#12", "t", found, out=io.StringIO()), 1)

    def test_json_report(self):
        found = [(NOTE, "CLA: CONTRIBUTING.md:3: sign it"), (LOOK, "bob said they would take it")]
        out = io.StringIO()
        self.assertEqual(report("o/r#12", "Crash", found, as_json=True, out=out), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["verdict"], "look")
        self.assertEqual([f["level"] for f in result["findings"]], [LOOK, NOTE])

    def test_own_pr_is_only_a_note(self):
        found = assess(data(closedByPullRequestsReferences={"nodes": [pr(40, author="me")]}), "o", "r", 12)
        self.assertEqual(levels(found), [NOTE])

    def test_ignore_skips_a_pr(self):
        found = assess(data(closedByPullRequestsReferences={"nodes": [pr(40)]}), "o", "r", 12, ignore={40})
        self.assertEqual(found, [])

    def test_mention_without_closing_keyword_is_look(self):
        mentions = {
            "nodes": [
                pr(41, body="Related to #12"),
                pr(42, body="See #123"),
                pr(43, body="Fixes other/r#12"),
                pr(45, body="Fixes foo/r#12"),
            ]
        }
        found = assess(data(extra={"mentions": mentions}), "o", "r", 12)
        self.assertEqual(found, [(LOOK, "open PR mentions this issue: #41 by alice: Fix it")])

    def test_mention_with_closing_keyword_stops(self):
        mentions = {"nodes": [pr(41, body="Fixes #12"), pr(44, body="closes https://github.com/o/r/issues/12")]}
        found = assess(data(extra={"mentions": mentions}), "o", "r", 12)
        self.assertEqual(levels(found), [STOP, STOP])

    def test_merged_and_closed_prs(self):
        nodes = [pr(50, state="MERGED"), pr(51, state="CLOSED")]
        found = assess(data(closedByPullRequestsReferences={"nodes": nodes}), "o", "r", 12)
        self.assertEqual(levels(found), [LOOK, NOTE])

    def test_closed_issue_assignee_and_label(self):
        found = assess(
            data(
                state="CLOSED",
                stateReason="NOT_PLANNED",
                assignees={"nodes": [{"login": "bob"}]},
                labels={"nodes": [{"name": "status: wontfix"}, {"name": "status: unblocked"}]},
            ),
            "o",
            "r",
            12,
        )
        self.assertEqual(
            found,
            [
                (STOP, "the issue is closed (not planned)"),
                (STOP, "assigned to bob"),
                (LOOK, "labeled 'status: wontfix'"),
            ],
        )

    def test_help_wanted_assignee_is_a_look(self):
        found = assess(
            data(assignees={"nodes": [{"login": "owner"}]}, labels={"nodes": [{"name": "help wanted"}]}),
            "o",
            "r",
            12,
        )
        self.assertEqual(levels(found), [LOOK])
        self.assertIn("assigned to owner, but labeled 'help wanted'", found[0][1])

    def test_already_fixed_comment_is_a_look(self):
        comments = [comment("carol", "Looks like this is already fixed on main by #14825.")]
        found = assess(
            data(body="This was fixed on main once and broke again.", comments={"nodes": comments}), "o", "r", 12
        )
        self.assertEqual(
            found,
            [
                (
                    LOOK,
                    "carol says it may already be fixed on 2026-10-20: "
                    '"Looks like this is already fixed on main by #14825."',
                )
            ],
        )

    def test_links_to_other_repositories_are_not_mentions(self):
        body = "Bumps codeql-action.\n- Fix it ([#12](https://github.com/github/codeql-action/pull/12))"
        html = '<li>Fixed. <a href="https://redirect.github.com/github/codeql-action/pull/12">#12</a></li>'
        mentions = {
            "nodes": [pr(41, body=body), pr(43, body=html), pr(42, body="See [#12](https://github.com/o/r/issues/12)")]
        }
        found = assess(data(extra={"mentions": mentions}), "o", "r", 12)
        self.assertEqual([text.split(":")[0] for _, text in found], ["open PR mentions this issue"])
        self.assertIn("#42", found[0][1])

    def test_claims_skip_bots_and_viewer_and_keep_one_per_person(self):
        comments = [
            comment("bob", "I'd like to work on this"),
            comment("bob", "I'm working on it now"),
            comment("me", "I'll take this"),
            comment("helper", "I'll take a look at the logs", kind="Bot"),
        ]
        found = assess(data(comments={"nodes": comments}), "o", "r", 12)
        self.assertEqual(found, [(LOOK, 'bob said they would take it on 2026-10-20: "I\'m working on it now"')])

    def test_keyword_hits_skip_reported_and_own_prs(self):
        extra = {"kw0": {"nodes": [pr(40), pr(60, title="Fix crash on start"), pr(61, author="me")]}}
        found = assess(
            data(closedByPullRequestsReferences={"nodes": [pr(40)]}, extra=extra), "o", "r", 12, keywords=["crash"]
        )
        self.assertEqual(levels(found), [STOP, LOOK])
        self.assertIn("#60", found[1][1])


class PatternTest(unittest.TestCase):
    def test_claims(self):
        for text in (
            "I'd like to work on this",
            "Can I take this one?",
            "/assign",
            "please assign it to me",
            "- [x] I'm willing to submit a PR",
            "I'll open a PR for this tonight",
            "Let me pick it up",
            "I\u2019m fixing this in my fork",
            "The fix branch is ready: https://github.com/someone/repo/tree/fix-crash",
            "A fix is ready on my fork, opening the PR after lunch",
            "Happy to send a PR if you want it",
            "I'd like to pick this one up. Plan is to add the missing input.",
        ):
            self.assertEqual(claim_line(f"Hi\n{text}\nThanks"), text)
        for text in (
            "Same crash here",
            "Any update?",
            "- [ ] I'm willing to submit a PR",
            "Works for me.",
            "I am working on a Next.js app and the build fails after upgrading.",
            "I'm trying to fix my config but it keeps crashing",
            "Can I take a screenshot of the error somehow?",
            "I will submit more logs tomorrow",
            "I'll take a look at the logs",
            "When a fix is ready, please ping me.",
            "Is the fix branch ready?",
            "We'd be happy to review a PR.",
        ):
            self.assertIsNone(claim_line(text), text)

    def test_rules(self):
        files = {
            "CONTRIBUTING.md": (
                "Use of AI tools must be disclosed.\nSign the CLA first.\nWait to be assigned before you open a PR.\n"
            ),
            ".github/pull_request_template.md": (
                "Signed-off-by: line required (DCO)\nWe use CopilotKit and the mail api.\n"
            ),
            "AGENTS.md": "This is an AI agent framework.\nThe issue must carry the `accepted` label.\n",
        }
        found = [(level, text.split(":")[0]) for level, text in rule_findings(files)]
        self.assertEqual(
            found,
            [(LOOK, "AI policy"), (NOTE, "CLA"), (NOTE, "DCO"), (LOOK, "issue gate"), (LOOK, "issue gate")],
        )

    def test_rule_levels(self):
        cases = [
            ("Pull requests from AI agents will be closed.", [(LOOK, "AI policy")]),
            ("We do not ban AI tools; just tell us when you used them.", [(NOTE, "AI policy")]),
            ("Inactive issues are automatically closed after 30 days by the stale bot.", []),
            ("Run the tests before a reviewer is assigned to your PR.", []),
            ("Wait to be assigned before you open a PR.", [(LOOK, "issue gate")]),
            ("Before you start, make sure the issue is assigned to you.", [(LOOK, "issue gate")]),
            ("issue must carry the `accepted` label. A maintainer applies `accepted` once we", [(LOOK, "issue gate")]),
            ("Pull requests that don't link an accepted issue are closed automatically by the", [(LOOK, "issue gate")]),
            ("Pull requests are never closed automatically, a maintainer reads each one.", []),
            ("Stale PRs aren't closed automatically.", []),
            ("Tick the box to disclose AI use.", [(LOOK, "AI policy")]),
            (
                "AI generated PRs may be accepted, but only if a maintainer has responded on an issue.",
                [(LOOK, "AI policy")],
            ),
            ("We do not accept, under any circumstances, **bulk, agent-driven contributions**", [(LOOK, "AI policy")]),
            ("Autonomous coding agents must not open pull requests here.", [(LOOK, "AI policy")]),
        ]
        for line, expected in cases:
            found = [(level, text.split(":")[0]) for level, text in rule_findings({"CONTRIBUTING.md": line})]
            self.assertEqual(found, expected, line)

    def test_ai_ban_is_look_and_survives_the_cut(self):
        files = {
            "CONTRIBUTING.md": (
                "AI tools are fine for docs, see the rule below.\n"
                "Use of AI is allowed in tests.\n"
                "We do not accept AI-generated pull requests.\n"
            )
        }
        self.assertEqual(
            rule_findings(files),
            [
                (LOOK, "AI policy: CONTRIBUTING.md:3: We do not accept AI-generated pull requests."),
                (NOTE, "AI policy: CONTRIBUTING.md:1: AI tools are fine for docs, see the rule below."),
                (NOTE, "AI policy: 1 more line"),
            ],
        )

    def test_linked_guide_shows_the_link(self):
        line = (
            "* Please refer to the article on [creating pull requests]"
            "(https://github.com/microsoft/vscode/wiki/How-to-Contribute#pull-requests)"
            " and contributing to this project."
        )
        self.assertEqual(
            rule_findings({"CONTRIBUTING.md": f"# Contributing\n{line}\nSee https://example.com/docs too.\n"}),
            [
                (
                    NOTE,
                    "linked guide: CONTRIBUTING.md:2: "
                    "https://github.com/microsoft/vscode/wiki/How-to-Contribute#pull-requests",
                )
            ],
        )

    def test_targets(self):
        self.assertEqual(TARGET.match("o/r#12").group("owner", "name", "num"), ("o", "r", "12"))
        self.assertEqual(
            TARGET.match("https://github.com/o/r.js/issues/12").group("owner", "name", "num"), ("o", "r.js", "12")
        )
        self.assertEqual(TARGET.match("o/r").group("num"), None)
        self.assertIsNone(TARGET.match("https://github.com/o/r/pull/12"))
        for link in (
            "https://github.com/o/r/issues/12#issuecomment-99",
            "https://github.com/o/r/issues/12?notification_referrer_id=x",
            "github.com/o/r/issues/12",
        ):
            self.assertEqual(TARGET.match(link).group("owner", "name", "num"), ("o", "r", "12"), link)

    def test_query_declares_only_used_variables(self):
        self.assertNotIn("$num", build_query(False, 0))
        query = build_query(True, 2)
        for var in ("$num", "$mentions", "$kw0", "$kw1"):
            self.assertEqual(query.count(var), 2, var)


def issue_node(number, stars=5000, labels=(), comments=(), pr_states=()):
    return {
        "number": number,
        "title": f"Bug {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "createdAt": "2026-10-20T10:00:00Z",
        "body": "It breaks.",
        "author": {"login": "reporter", "__typename": "User"},
        "repository": {"nameWithOwner": "o/r", "stargazerCount": stars},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"totalCount": len(comments), "nodes": list(comments)},
        "timelineItems": {"nodes": [{"source": {"state": state}} for state in pr_states]},
    }


def search_reply(nodes, more=False):
    return {"viewer": {"login": "me"}, "search": {"pageInfo": {"hasNextPage": more, "endCursor": "c1"}, "nodes": nodes}}


class FindTest(unittest.TestCase):
    def test_query(self):
        query = cli.find_query(
            ["memory", "leak"], ["o/r"], ['good "first" issue'], "python", 30, 3, today=date(2026, 10, 24)
        )
        self.assertEqual(
            query,
            "is:issue is:open no:assignee -linked:pr archived:false created:>=2026-09-24 comments:<=3 "
            'sort:created-desc repo:o/r label:"good first issue" language:python memory leak',
        )

    def test_help_wanted_query_keeps_assigned_issues(self):
        query = cli.find_query([], ["o/r"], ["help wanted"], None, 30, 3, today=date(2026, 10, 24))
        self.assertNotIn("no:assignee", query)
        self.assertIn("no:assignee", cli.find_query([], ["o/r"], ["bug"], None, 30, 3, today=date(2026, 10, 24)))

    def test_open_for_work(self):
        self.assertTrue(cli.open_for_work(issue_node(1), "me"))
        self.assertTrue(cli.open_for_work(issue_node(2, pr_states=["CLOSED"]), "me"))
        self.assertFalse(cli.open_for_work(issue_node(3, labels=["blocked"]), "me"))
        self.assertFalse(cli.open_for_work(issue_node(4, pr_states=["OPEN"]), "me"))
        self.assertFalse(cli.open_for_work(issue_node(5, comments=[comment("bob", "Can I take this?")]), "me"))
        self.assertTrue(cli.open_for_work(issue_node(6, comments=[comment("me", "I'll take this")]), "me"))

    def test_filters_stars_and_stops_at_the_limit(self):
        nodes = [issue_node(1, stars=10), issue_node(2), issue_node(3, labels=["duplicate"]), {}, issue_node(4)]
        code, out, _, graphql = run_main(
            ["find", "--repo", "o/r", "--min-stars", "100", "--limit", "2", "--json"],
            search_reply([*nodes, issue_node(5)], more=True),
        )
        self.assertEqual(code, 0)
        self.assertEqual([issue["target"] for issue in json.loads(out)["issues"]], ["o/r#2", "o/r#4"])
        self.assertEqual(graphql.call_count, 1)

    def test_min_stars_picks_repositories_first(self):
        repos = {"search": {"nodes": [{"nameWithOwner": f"o/r{i}"} for i in range(12)]}}
        code, out, _, graphql = run_main(
            ["find", "--min-stars", "1000", "--json"], [repos, search_reply([issue_node(1)]), search_reply([])]
        )
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual([issue["target"] for issue in result["issues"]], ["o/r#1"])
        self.assertEqual(len(result["queries"]), 2)
        self.assertIn("repo:o/r9 ", result["queries"][0] + " ")
        self.assertIn("repo:o/r11", result["queries"][1])
        self.assertIn("stars:>=1000", graphql.call_args_list[0].args[1]["q"])

    def test_repo_query(self):
        self.assertEqual(
            cli.repo_query(1000, "python", ["Good First Issue"], 30, today=date(2026, 10, 24)),
            "stars:>=1000 archived:false pushed:>=2026-09-24 sort:updated language:python good-first-issues:>0",
        )

    def test_text_output_points_to_the_check(self):
        code, out, _, _ = run_main(["find", "--repo", "o/r"], search_reply([issue_node(7)]))
        self.assertEqual(code, 0)
        self.assertIn("o/r#7", out)
        self.assertIn("next: run `calldibs o/r#7`", out)

    def test_limit_is_capped(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.main(["find", "--limit", "31"])
        self.assertEqual(raised.exception.code, 2)

    def test_negative_numbers_are_rejected(self):
        for option in ("--days", "--max-comments", "--min-stars"):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                cli.main(["find", option, "-1"])
            self.assertEqual(raised.exception.code, 2, option)
            self.assertIn("negative", err.getvalue(), option)


def run_main(argv, reply):
    """Run main() with GitHub replaced by `reply`: a dict, a list of dicts (one per request), or an exception."""
    out, err = io.StringIO(), io.StringIO()
    side_effect = reply if isinstance(reply, (Exception, list)) else None
    with (
        mock.patch.object(cli, "get_token", return_value="token"),
        mock.patch.object(cli, "graphql", return_value=reply, side_effect=side_effect) as graphql,
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue(), graphql


class MainTest(unittest.TestCase):
    def test_issue_link_with_keyword_as_json(self):
        reply = data(closedByPullRequestsReferences={"nodes": [pr(40)]})
        code, out, _, graphql = run_main(
            ["https://github.com/o/r/issues/12#issuecomment-5", "--json", "-k", 'cr"ash'], reply
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["verdict"], "stop")
        variables = graphql.call_args.args[1]
        self.assertEqual(variables["num"], 12)
        self.assertIn("sort:updated-desc", variables["mentions"])
        self.assertEqual(variables["kw0"], 'repo:o/r is:pr is:open in:title "crash"')

    def test_rules_only_as_json(self):
        reply = {
            "viewer": {"login": "me"},
            "repository": {"nameWithOwner": "o/r", "isArchived": False, "f0": {"text": "Sign the CLA first."}},
        }
        code, out, _, _ = run_main(["o/r", "--json"], reply)
        self.assertEqual(code, 0)
        rules = [{"level": NOTE, "message": "CLA: CONTRIBUTING.md:1: Sign the CLA first."}]
        self.assertEqual(json.loads(out), {"target": "o/r", "rules": rules})

    def test_pull_request_number_is_an_error(self):
        reply = {"viewer": {"login": "me"}, "repository": {"nameWithOwner": "o/r", "isArchived": False, "issue": None}}
        code, out, err, _ = run_main(["o/r#7", "--json"], reply)
        self.assertEqual(code, 2)
        self.assertIn("not an issue", json.loads(out)["error"])
        self.assertIn("not an issue", err)

    def test_api_error_is_exit_2(self):
        code, out, err, _ = run_main(["o/r#7"], cli.Error("GitHub API returned HTTP 401"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("HTTP 401", err)

    def test_http_error_hint(self):
        error = urllib.error.HTTPError(cli.API, 401, "Unauthorized", {}, None)
        with (
            mock.patch.object(cli.urllib.request, "urlopen", side_effect=error),
            self.assertRaisesRegex(cli.Error, "HTTP 401: the token is invalid or expired"),
        ):
            cli.graphql("query { viewer { login } }", {}, "token")

    def test_gh_token_timeout(self):
        with (
            mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}),
            mock.patch.object(cli.shutil, "which", return_value="/usr/bin/gh"),
            mock.patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 10)),
            self.assertRaisesRegex(cli.Error, "did not answer"),
        ):
            cli.get_token()


if __name__ == "__main__":
    unittest.main()
