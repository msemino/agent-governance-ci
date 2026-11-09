"""Each test breaks one guarantee and asserts the check catches it -- or keeps a valid
setup and asserts the check stays quiet. A check that only ever passes proves nothing,
and one that fires on valid input teaches people to ignore it; both directions are tested.
"""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agentgov  # noqa: E402

TODAY = datetime.date(2026, 11, 9)
LATER = "2026-11-30"
EARLIER = "2026-11-01"

CONFIG = """\
[rules]
canonical = "RULES.md"

[adapters]
files = ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]
required = ["CLAUDE.md", "AGENTS.md"]
max_lines = 6

[claims]
file = "coordination/claims.toml"
agents = ["claude", "codex"]
commit_tag = '^\\[(?P<agent>[a-z0-9-]+)\\]'
untagged = "warn"
"""

POINTER = "# Agent instructions\n\nRead [RULES.md](RULES.md) before doing anything.\n"


def claim(area, agent="claude", status="active", expires=LATER, extra=""):
    return textwrap.dedent(f"""\
        [[claim]]
        area = "{area}"
        agent = "{agent}"
        scope = "test"
        expires = {expires}
        status = "{status}"
        {extra}
        """)


class Sandbox(unittest.TestCase):
    """A throwaway repository that is valid until a test breaks one thing in it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agentgov-")
        self.write("agentgov.toml", CONFIG)
        self.write("RULES.md", "# Rules\n\n1. One rule, written once.\n")
        self.write("CLAUDE.md", POINTER)
        self.write("AGENTS.md", POINTER)
        self.write("coordination/claims.toml", "")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def run_checks(self, rev_range=None):
        return agentgov.run(self.root, rev_range=rev_range, today=TODAY)

    def failures(self, report, check=None):
        return [f for f in report.failures if check is None or f.check == check]

    def assertClean(self, report):
        self.assertEqual([], [str(f) for f in report.failures])


class TestBaseline(Sandbox):
    def test_valid_setup_is_quiet(self):
        self.assertClean(self.run_checks())


class TestCanonical(Sandbox):
    def test_missing_canonical_fails(self):
        os.remove(os.path.join(self.root, "RULES.md"))
        self.assertTrue(self.failures(self.run_checks(), "G1"))

    def test_empty_canonical_fails(self):
        self.write("RULES.md", "   \n\n")
        self.assertTrue(self.failures(self.run_checks(), "G1"))


class TestAdapters(Sandbox):
    def test_missing_required_adapter_fails(self):
        os.remove(os.path.join(self.root, "AGENTS.md"))
        self.assertTrue(self.failures(self.run_checks(), "G2"))

    def test_optional_adapter_may_be_absent(self):
        # GEMINI.md is listed but not required: its absence is not an error.
        self.assertClean(self.run_checks())

    def test_adapter_grown_into_a_manual_fails(self):
        body = POINTER + "".join(f"- rule {i}\n" for i in range(10))
        self.write("CLAUDE.md", body)
        self.assertTrue(self.failures(self.run_checks(), "G2"))

    def test_blank_lines_do_not_count(self):
        self.write("CLAUDE.md", POINTER + "\n" * 40)
        self.assertClean(self.run_checks())

    def test_adapter_that_does_not_point_at_canonical_fails(self):
        self.write("AGENTS.md", "# Agent instructions\n\nBe careful.\n")
        self.assertTrue(self.failures(self.run_checks(), "G2"))

    def test_optional_adapter_present_is_also_checked(self):
        self.write("GEMINI.md", "# Gemini\n\nMy own rules live here.\n")
        self.assertTrue(self.failures(self.run_checks(), "G2"))


class TestClaimSchema(Sandbox):
    def test_missing_field_fails(self):
        self.write("coordination/claims.toml",
                   '[[claim]]\narea = "src"\nagent = "claude"\nstatus = "active"\n')
        self.assertTrue(self.failures(self.run_checks(), "G3"))

    def test_unknown_status_fails(self):
        self.write("coordination/claims.toml", claim("src", status="paused"))
        self.assertTrue(self.failures(self.run_checks(), "G3"))

    def test_unknown_agent_fails(self):
        self.write("coordination/claims.toml", claim("src", agent="gpt"))
        self.assertTrue(self.failures(self.run_checks(), "G3"))

    def test_expires_must_be_a_date(self):
        self.write("coordination/claims.toml", claim("src", expires='"next week"'))
        self.assertTrue(self.failures(self.run_checks(), "G3"))

    def test_missing_claims_file_fails(self):
        os.remove(os.path.join(self.root, "coordination/claims.toml"))
        self.assertTrue(self.failures(self.run_checks(), "G3"))

    def test_invalid_toml_fails_instead_of_crashing(self):
        self.write("coordination/claims.toml", "[[claim]\narea = ")
        self.assertTrue(self.failures(self.run_checks(), "G3"))


class TestClaimConflicts(Sandbox):
    def conflicts(self, *claims):
        self.write("coordination/claims.toml", "\n".join(claims))
        return self.failures(self.run_checks(), "G4")

    def test_same_area_twice_fails(self):
        self.assertTrue(self.conflicts(claim("src/billing"), claim("src/billing", agent="codex")))

    def test_wildcard_overlaps_everything(self):
        self.assertTrue(self.conflicts(claim("*"), claim("docs", agent="codex")))

    def test_parent_and_child_overlap(self):
        self.assertTrue(self.conflicts(claim("src"), claim("src/billing", agent="codex")))

    def test_trailing_slash_is_the_same_area(self):
        self.assertTrue(self.conflicts(claim("src/billing/"), claim("src/billing", agent="codex")))

    def test_shared_string_prefix_is_not_overlap(self):
        # 'src/bill' and 'src/billing' share characters, not a directory.
        self.assertFalse(self.conflicts(claim("src/bill"), claim("src/billing", agent="codex")))

    def test_disjoint_areas_coexist(self):
        self.assertFalse(self.conflicts(claim("src/billing"), claim("docs", agent="codex")))

    def test_closed_claim_never_conflicts(self):
        self.assertFalse(self.conflicts(claim("*", status="closed"), claim("src", agent="codex")))

    def test_expired_active_claim_fails(self):
        self.assertTrue(self.conflicts(claim("src", expires=EARLIER)))

    def test_expired_closed_claim_is_history(self):
        # A finished claim turning the build red would be a red that means nothing.
        self.assertFalse(self.conflicts(claim("src", status="closed", expires=EARLIER)))


class TestCommitsAgainstClaims(Sandbox):
    """G5: commits in a range are checked against the active claims of other agents."""

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message, *paths):
        for p in paths:
            self.write(p, f"{message}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        super().setUp()
        self.write("coordination/claims.toml", claim("src/billing", agent="claude"))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.base = self.commit("[claude] baseline", "README.md")

    def g5(self, untagged=None):
        if untagged:
            self.write("agentgov.toml", CONFIG.replace('untagged = "warn"', f'untagged = "{untagged}"'))
        return self.run_checks(rev_range=f"{self.base}..HEAD")

    def test_other_agent_inside_claimed_area_fails(self):
        self.commit("[codex] tweak billing", "src/billing/rates.py")
        self.assertTrue(self.failures(self.g5(), "G5"))

    def test_owner_inside_claimed_area_passes(self):
        self.commit("[claude] split extract from decide", "src/billing/rates.py")
        self.assertClean(self.g5())

    def test_other_agent_outside_claimed_area_passes(self):
        self.commit("[codex] docs", "docs/usage.md")
        self.assertClean(self.g5())

    def test_sibling_with_shared_prefix_passes(self):
        self.commit("[codex] unrelated module", "src/billing_export/x.py")
        self.assertClean(self.g5())

    def test_claims_file_is_always_writable(self):
        # Agents must be able to add or close their own claims.
        self.write("coordination/claims.toml",
                   claim("src/billing", agent="claude") + claim("docs", agent="codex"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[codex] claim docs")
        self.assertClean(self.g5())

    def test_untagged_commit_warns_by_default(self):
        self.commit("hotfix by a human", "src/billing/rates.py")
        report = self.g5()
        self.assertClean(report)
        self.assertTrue(any(w.check == "G5" for w in report.warnings))

    def test_untagged_commit_can_be_made_to_fail(self):
        self.commit("hotfix by a human", "src/billing/rates.py")
        self.assertTrue(self.failures(self.g5(untagged="fail"), "G5"))

    def test_unknown_agent_tag_fails(self):
        self.commit("[gemini] tweak", "src/billing/rates.py")
        self.assertTrue(self.failures(self.g5(), "G5"))

    def test_bad_range_fails_instead_of_passing(self):
        report = self.run_checks(rev_range="nope..HEAD")
        self.assertTrue(self.failures(report, "G5"))


class TestCli(Sandbox):
    def test_exit_code_reflects_failures(self):
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agentgov.py")
        ok = subprocess.run([sys.executable, script, "--root", self.root, "--today", "2026-11-09"],
                            capture_output=True, text=True)
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)
        os.remove(os.path.join(self.root, "RULES.md"))
        bad = subprocess.run([sys.executable, script, "--root", self.root, "--today", "2026-11-09"],
                             capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertIn("G1", bad.stdout)


if __name__ == "__main__":
    unittest.main()
