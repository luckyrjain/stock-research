# Squad Map

## MCP profile

| Source | Status |
|--------|--------|
| GitLab | Not configured — this repo is hosted without a connected GitLab MCP in this environment |
| Datadog | Configured but unauthenticated in this session (`plugin:datadog:mcp` failed to connect: ENOTFOUND) |
| CODEOWNERS | Not present in repo (`find . -iname CODEOWNERS` → no match) |

## Squad snapshot

**single-operator project, no squad structure.**

Evidence: `docs/PRD.md` §17.1 explicitly accepts a "bus factor of one" for this project; `git log` author
history and `CLAUDE.md` (repo root) both describe a single engineer operating this codebase with Claude Code
as the primary implementation tool. No `CODEOWNERS` file, no GitLab/Jira project, no team directory exist in
the repository.

| Repo | GitLab squad | Datadog team | Owner | Confidence | Evidence |
|------|---------------|--------------|-------|------------|----------|
| stock-research | N/A — no GitLab | N/A — Datadog unauthenticated this session | Lucky Jain (sole operator) | HIGH | `docs/PRD.md` §17.1 (bus factor of one); repo has no `CODEOWNERS`; git config user in session context is "Lucky Jain" |

Per `session-0b.md` step "Both unavailable → run CODEOWNERS fallback when possible": CODEOWNERS is absent,
so ownership defaults to the sole repository operator for every in-scope path. This is recorded as the
Session 0b result rather than blocking the engagement, per this run's explicit single-operator adaptation.
