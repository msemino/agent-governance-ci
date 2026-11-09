#!/usr/bin/env python3
"""agentgov -- CI checks for a repository that more than one coding agent writes to.

An instruction file (CLAUDE.md, AGENTS.md, ...) can be read and still ignored. These checks
run in CI, the one place every agent's work passes through whether or not its client
supports local hooks, and turn the rules into failures that cannot be skipped:

  G1  the canonical rules file exists and is not empty
  G2  each agent's instruction file is a pointer to it, not a second copy of the rules
  G3  claims are well formed: area, agent, expiry, status
  G4  no two active claims overlap, and no active claim has expired
  G5  (with --range) no commit touches an area actively claimed by a different agent

Standard library only (Python 3.11+, for tomllib). If it needed an install it would stop
running somewhere, and a check that silently does not run is worse than none.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import re
import subprocess
import sys
import tomllib

STATUSES = {"active", "closed"}
REQUIRED_CLAIM_FIELDS = ("area", "agent", "expires", "status")
UNTAGGED_MODES = {"ignore", "warn", "fail"}

CHECKS = {
    "G1": "canonical rules file exists and is not empty",
    "G2": "agent instruction files are pointers to the canonical file",
    "G3": "claims are well formed",
    "G4": "active claims do not overlap and have not expired",
    "G5": "commits in --range stay out of areas claimed by other agents",
}


@dataclasses.dataclass(frozen=True)
class Finding:
    check: str
    message: str

    def __str__(self):
        return f"{self.check}  {self.message}"


@dataclasses.dataclass
class Report:
    failures: list = dataclasses.field(default_factory=list)
    warnings: list = dataclasses.field(default_factory=list)
    passed: list = dataclasses.field(default_factory=list)

    def fail(self, check, message):
        self.failures.append(Finding(check, message))

    def warn(self, check, message):
        self.warnings.append(Finding(check, message))


# --------------------------------------------------------------------------- config

DEFAULTS = {
    "rules": {"canonical": "RULES.md"},
    "adapters": {
        "files": ["CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md"],
        "required": ["CLAUDE.md", "AGENTS.md"],
        "max_lines": 20,
    },
    "claims": {
        "file": "coordination/claims.toml",
        "agents": ["claude", "codex"],
        "commit_tag": r"^\[(?P<agent>[a-z0-9-]+)\]",
        "untagged": "warn",
    },
}


def load_config(root):
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    path = os.path.join(root, "agentgov.toml")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
        for section, values in user.items():
            cfg.setdefault(section, {}).update(values)
    return cfg


# --------------------------------------------------------------------------- areas

def norm_area(area):
    area = area.strip().replace("\\", "/")
    return "*" if area == "*" else area.strip("/")


def area_contains(area, path):
    """True if `path` (a file or another area) lies inside `area`.

    Compared by path segment, not by characters: 'src/bill' does not contain
    'src/billing'. A plain startswith() would make two unrelated modules collide."""
    area, path = norm_area(area), norm_area(path)
    if area == "*":
        return True
    return path == area or path.startswith(area + "/")


def areas_overlap(a, b):
    return area_contains(a, b) or area_contains(b, a)


# --------------------------------------------------------------------------- G1, G2

def check_canonical(root, cfg, report):
    canonical = cfg["rules"]["canonical"]
    path = os.path.join(root, canonical)
    if not os.path.exists(path):
        report.fail("G1", f"{canonical} is missing: the adapters point at nothing")
        return
    with open(path, encoding="utf-8") as fh:
        if not fh.read().strip():
            report.fail("G1", f"{canonical} is empty")
            return
    report.passed.append("G1")


def check_adapters(root, cfg, report):
    canonical = cfg["rules"]["canonical"]
    target = os.path.basename(canonical)
    limit = int(cfg["adapters"]["max_lines"])
    required = set(cfg["adapters"]["required"])
    before = len(report.failures)
    for name in cfg["adapters"]["files"]:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            if name in required:
                report.fail("G2", f"{name} is missing (required adapter)")
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        useful = [line for line in text.splitlines() if line.strip()]
        if len(useful) > limit:
            report.fail("G2", f"{name} has {len(useful)} non-blank lines (limit {limit}): "
                              f"it is turning into a second copy of the rules -- move them to {canonical}")
        if target not in text:
            report.fail("G2", f"{name} does not reference {canonical}: a pointer has to point")
    if len(report.failures) == before:
        report.passed.append("G2")


# --------------------------------------------------------------------------- G3, G4

def load_claims(root, cfg, report):
    rel = cfg["claims"]["file"]
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        report.fail("G3", f"{rel} is missing: without it two agents can collide in silence")
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        report.fail("G3", f"{rel} is not valid TOML: {exc}")
        return None
    return data.get("claim", [])


def check_claim_schema(claims, cfg, report):
    agents = set(cfg["claims"]["agents"])
    valid = []
    before = len(report.failures)
    for i, c in enumerate(claims, 1):
        label = f"claim #{i} ({c.get('area', '?')})"
        missing = [f for f in REQUIRED_CLAIM_FIELDS if f not in c]
        if missing:
            report.fail("G3", f"{label} is missing {', '.join(missing)}")
            continue
        if c["status"] not in STATUSES:
            report.fail("G3", f"{label} has status '{c['status']}' (expected: active | closed)")
            continue
        if c["agent"] not in agents:
            report.fail("G3", f"{label} names agent '{c['agent']}', not one of {sorted(agents)}")
            continue
        if not isinstance(c["expires"], datetime.date) or isinstance(c["expires"], datetime.datetime):
            report.fail("G3", f"{label} has expires = {c['expires']!r}: use a TOML date, e.g. 2026-11-30")
            continue
        valid.append(c)
    if len(report.failures) == before:
        report.passed.append("G3")
    return valid


def check_claim_conflicts(claims, today, report):
    active = [c for c in claims if c["status"] == "active"]
    before = len(report.failures)
    for c in active:
        if c["expires"] < today:
            report.fail("G4", f"active claim on '{c['area']}' by {c['agent']} expired {c['expires']}: "
                              "renew it or close it")
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if areas_overlap(a["area"], b["area"]):
                report.fail("G4", f"active claims overlap: '{a['area']}' ({a['agent']}) and "
                                  f"'{b['area']}' ({b['agent']}) -- resolve before either proceeds")
    if len(report.failures) == before:
        report.passed.append("G4")
    return [c for c in active if c["expires"] >= today]


# --------------------------------------------------------------------------- G5

def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def check_commits(root, cfg, active, rev_range, report):
    agents = set(cfg["claims"]["agents"])
    tag = re.compile(cfg["claims"]["commit_tag"])
    mode = cfg["claims"].get("untagged", "warn")
    if mode not in UNTAGGED_MODES:
        report.fail("G5", f"untagged = '{mode}' is not one of {sorted(UNTAGGED_MODES)}")
        return
    claims_file = norm_area(cfg["claims"]["file"])

    listed = git(root, "rev-list", "--reverse", rev_range)
    if listed.returncode != 0:
        # An unreadable range must not read as "no commits, all clear".
        report.fail("G5", f"cannot read range {rev_range}: {listed.stderr.strip()}")
        return

    before = len(report.failures)
    for sha in listed.stdout.split():
        subject = git(root, "log", "-1", "--format=%s", sha).stdout.strip()
        files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).stdout.split()
        files = [f for f in files if norm_area(f) != claims_file]
        m = tag.search(subject)
        short = sha[:7]
        if not m:
            hits = [f for f in files if any(area_contains(c["area"], f) for c in active)]
            if hits and mode != "ignore":
                msg = f"{short} has no agent tag and touches claimed area(s): {', '.join(hits[:3])}"
                (report.fail if mode == "fail" else report.warn)("G5", msg)
            continue
        agent = m.group("agent")
        if agent not in agents:
            report.fail("G5", f"{short} is tagged [{agent}], not one of {sorted(agents)}")
            continue
        for f in files:
            for c in active:
                if c["agent"] != agent and area_contains(c["area"], f):
                    report.fail("G5", f"{short} by {agent} touches {f}, claimed by {c['agent']} "
                                      f"until {c['expires']} ({c.get('scope', 'no scope given')})")
    if len(report.failures) == before:
        report.passed.append("G5")


# --------------------------------------------------------------------------- run

def run(root, rev_range=None, today=None):
    today = today or datetime.date.today()
    report = Report()
    try:
        cfg = load_config(root)
    except tomllib.TOMLDecodeError as exc:
        report.fail("G0", f"agentgov.toml is not valid TOML: {exc}")
        return report
    check_canonical(root, cfg, report)
    check_adapters(root, cfg, report)
    claims = load_claims(root, cfg, report)
    if claims is None:
        return report
    valid = check_claim_schema(claims, cfg, report)
    active = check_claim_conflicts(valid, today, report)
    if rev_range:
        check_commits(root, cfg, active, rev_range, report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", default=".", help="repository root (default: .)")
    p.add_argument("--range", dest="rev_range", help="git range to check against claims, e.g. origin/main..HEAD")
    p.add_argument("--today", help="override today's date (YYYY-MM-DD), for reproducible runs")
    p.add_argument("--list", action="store_true", help="list the checks and exit")
    args = p.parse_args(argv)

    if args.list:
        for k, v in CHECKS.items():
            print(f"{k}  {v}")
        return 0

    today = datetime.date.fromisoformat(args.today) if args.today else None
    report = run(os.path.abspath(args.root), rev_range=args.rev_range, today=today)
    for k in report.passed:
        print(f"ok    {k}  {CHECKS.get(k, '')}")
    for w in report.warnings:
        print(f"WARN  {w}")
    for f in report.failures:
        print(f"FAIL  {f}")
    print(f"\n{len(report.failures)} failure(s), {len(report.warnings)} warning(s)")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
