# Rules for agents working in this repository

These rules are written once, here. `CLAUDE.md` and `AGENTS.md` only point at this file, and CI fails if either grows into a second copy.

1. **Tests first, and red.** A test that passes the moment it is written has not been shown to test anything. Break the code, watch it fail, then fix it.
2. **Claim before structural work.** Anything that changes more than one area, or changes how an area is organised, starts with an entry in `coordination/claims.toml`: area, agent, scope, expiry. Close it in the same change that finishes the work.
3. **Tag every commit with the agent that made it**: `[claude] ...`, `[codex] ...`. Humans may leave commits untagged.
4. **Standard library only.** A check that needs an install stops running somewhere, and a check that silently does not run is worse than none.
5. **A red must mean something.** If a check fails on valid input, fix the check before anything else: a check that cries wolf teaches everyone to ignore it.
