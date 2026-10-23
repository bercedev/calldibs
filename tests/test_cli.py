import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
                labels={"nodes": [{"name": "status: wontfix"}]},
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
            "I\u2019m fixing this in my fork",
        ):
            self.assertEqual(claim_line(f"Hi\n{text}\nThanks"), text)
        for text in ("Same crash here", "Any update?", "- [ ] I'm willing to submit a PR", "Works for me."):
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
        found = [text.split(":")[0] for _, text in rule_findings(files)]
        self.assertEqual(found, ["AI policy", "CLA", "DCO", "issue gate", "issue gate"])

    def test_targets(self):
        self.assertEqual(TARGET.match("o/r#12").group("owner", "name", "num"), ("o", "r", "12"))
        self.assertEqual(
            TARGET.match("https://github.com/o/r.js/issues/12").group("owner", "name", "num"), ("o", "r.js", "12")
        )
        self.assertEqual(TARGET.match("o/r").group("num"), None)
        self.assertIsNone(TARGET.match("https://github.com/o/r/pull/12"))

    def test_query_declares_only_used_variables(self):
        self.assertNotIn("$num", build_query(False, 0))
        query = build_query(True, 2)
        for var in ("$num", "$mentions", "$kw0", "$kw1"):
            self.assertEqual(query.count(var), 2, var)


if __name__ == "__main__":
    unittest.main()
