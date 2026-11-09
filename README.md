# agent-governance-ci

CI checks for a repository that more than one coding agent writes to.

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![runtime](https://img.shields.io/badge/runtime-stdlib%20only-informational)

```bash
python agentgov.py                                  # G1-G4 on the working tree
python agentgov.py --range origin/main..HEAD        # plus G5 on the commits of a branch
python -m unittest discover -s tests -v             # 34 tests, each breaks one guarantee
```

## Why it exists

Two coding agents from different vendors write to the same repository of mine. One day
both built an architecture for the same problem, in parallel, without knowing about each
other, and the two designs were opposite: one removed a duplication, the other managed it.
Each agent had an instruction file. Each had read it.

**An instruction file can be read and still ignored.** It is a request, not a control.
The fix was to move the rules into one place and make the repository itself refuse the
failure modes, in CI, where every agent's work passes whether or not its client supports
local hooks. One of my two agents has pre-commit hooks; the other does not. A control that
lives in a hook covers one of them.

This repository is the general version of those checks. In the private repository where
they started, the pointer and claims checks have run on every push since 31 August 2026.
**G5, commits against claims, is new in this extraction** and has not run there yet.

## What it checks

| | Check | Fails when |
|---|---|---|
| G1 | canonical rules file | `RULES.md` is missing or empty |
| G2 | adapters are pointers | `CLAUDE.md`, `AGENTS.md`… grows past a size limit, or does not reference `RULES.md` |
| G3 | claims are well formed | a claim lacks area, agent, expiry or status, names an unknown agent, or the file is not valid TOML |
| G4 | claims do not collide | two active claims overlap, or an active claim has expired |
| G5 | commits respect claims | a commit tagged `[codex]` touches an area that `claude` holds an active claim on |

A claim is an entry in `coordination/claims.toml`:

```toml
[[claim]]
area = "src/billing"          # path prefix; "*" claims the whole repository
agent = "claude"
scope = "move the notify decision out of the prompt"
expires = 2026-11-16
status = "active"             # active | closed
```

Because the file is versioned, two agents claiming the same area at the same time produce
a merge conflict or a red build. **The collision becomes visible instead of silent.**

## Decisions

**Areas are compared by directory segment, not by characters.** `src/bill` does not cover
`src/billing`. A plain `startswith()` makes two unrelated modules collide, and the test
suite includes a mutation of exactly that to prove the tests would catch it.

**A closed claim never fails the build, even long after its expiry.** A finished piece of
work turning CI red is a red that means nothing, and those teach people to ignore red.

**An unreadable git range fails.** If `--range` cannot be resolved, G5 does not report
"no commits, all clear".

**The claims file is always writable.** Agents have to be able to add and close claims,
including on areas someone else holds; G5 skips that one file.

**Untagged commits warn by default** (`untagged = "warn"`), because humans commit too.
Set `untagged = "fail"` for a repository only agents write to.

**Standard library only.** A check that needs an install stops running on some machine,
and a check that silently does not run is worse than having none.

## What it does not do

- **G2 is a size heuristic.** A pointer file with one extra rule in it stays under the
  limit. It catches the slow drift of an instruction file into a second manual — which is
  how the duplication happened — not a single sentence.
- **Agent identity is the commit tag, and the tag is self-declared.** This catches
  collisions between cooperating agents. It is not access control against a hostile one;
  that needs separate credentials per agent, branch protection and `CODEOWNERS`.
- **G5 judges every commit in the range against the claims file at the end of the range**,
  not as the file stood when each commit was made.
- **Claims are by path.** Two agents changing different files that must change together
  will not be flagged.

## Configuration

`agentgov.toml` at the repository root. Every key has a default; this repository uses its
own checks, so its `agentgov.toml`, `RULES.md`, `CLAUDE.md`, `AGENTS.md` and
`coordination/claims.toml` are the working example.

## License

MIT
