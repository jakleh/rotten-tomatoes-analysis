# New Feature Protocol

Every non-trivial feature follows this sequence. Do not skip steps or reorder them.

## Phase 1: Plan

1. **Write a plan doc** in `planning/plans/` (e.g., `planning/plans/feature_name.md`).
   - Context: why this feature exists, what problem it solves
   - Files to modify (table)
   - Implementation steps with code sketches
   - Risk assessment: what could go wrong, effort vs. impact
   - What this does NOT cover (explicit scope boundary)
2. **Present the plan to the user** for alignment before writing any code.

## Phase 2: Simulate Errors

3. **Create or update an error playbook** in `planning/errors/` following the established format:
   - Failure modes table (ID, description, likelihood, severity)
   - Prevention section (what we can guard against now)
   - Outside our control section (things we can only detect and diagnose)
   - Detection table (log signals mapped to failure IDs)
   - Diagnosis decision tree (flowchart for troubleshooting)
   - Key commands (verification and recovery)
   - Research links and cross-references to other playbooks
4. **Use a unique prefix** for the failure IDs (check existing playbooks to avoid collisions). Current prefixes: A (chrome), B (selenium), C (html_parsing), D (neon), E (cloud_run), F (cloud_scheduler), G (github_actions), H (backfill), I (monitoring), J (anti_blocking).

## Phase 3: Implement

5. **Write the code** following the plan.
6. **Write or update tests** covering new behavior.
7. **Run the full test suite** and confirm all tests pass.

## Phase 4: Validate

8. **Walk through the error playbook** and check each failure mode against the implementation:
   - Is each "Prevention" item actually implemented?
   - Are the "Detection" signals actually logged by the code?
   - Does the decision tree reference log messages that exist?
   - Are there failure modes the playbook missed?
9. **Fix any gaps** discovered during validation.
10. **Update docs**: CLAUDE.md, README.MD, error playbooks (cross-references), and the plan doc itself if the implementation diverged from the plan.

## When to Apply This Protocol

- Any change that touches scraping logic, database operations, CLI interface, or deployment
- Any change with failure modes that aren't immediately obvious from reading the code
- Any change the user explicitly asks to be planned

## When NOT to Apply

- Trivial fixes (typos, formatting, single-line bug fixes)
- Documentation-only changes
- Test-only changes
