# AGENTS.md

## Purpose
Reusable agent behavior guidance for Python CLI projects.
Keep this file general; put project-specific command/domain rules in project docs.

## Defaults
- Language: Python
- CLI framework: `argparse` (not Click)
- Package entrypoint pattern: `<package>.main:cli`
- Runtime dependencies are project-defined
- Project uses a dedicated conda environment

## Core Philosophy
- Simplicity first: maximize impact with minimal code.
- Clarity and consistency over cleverness.
- Build tools that are excellent for both humans and automation agents.
- Prefer explicit behavior over implicit behavior.

## Non-Negotiables
- Use the project conda environment for install/run/test.
- Install packages only into the project environment.
- Never install packages into system Python or conda `base` without explicit user approval.
- Do not assume backward compatibility by default.
- For potentially breaking changes, confirm policy first: strict cutover or compatibility window.
- Never commit secrets, live tokens, cookies, or credential-bearing env files.
- Keep runtime/cache artifacts untracked unless explicitly required.

## Interaction Rules
- Ask clarifying questions before implementation when instructions are ambiguous.
- For non-trivial changes, share a short plan first.
- After user feedback/corrections, summarize what changed and why.
- State trade-offs explicitly when recommending options.
- Never assume when multiple reasonable approaches exist; confirm direction.

## Test and Design Process
- Use `pytest` as the default test runner.
- Treat test conditions as part of design, not only post-implementation validation.
- For new functionality, add/update pytest cases defining expected behavior.
- For bug fixes, add a regression test that fails before the fix and passes after the fix.
- When behavior/contracts change, update tests in the same change set as code and docs.
- Prefer small, focused tests that clearly state one behavior per test.
- Keep tests deterministic; avoid live external dependencies for unit tests.

## Core Design Rules
- Default output should be clean, human-readable (table when appropriate).
- On relevant commands, support these flags consistently:
  - `--json`
  - `--yaml`
  - `--raw`
  - `--refresh` (when state/cache is involved)
- Help behavior should follow Unix conventions:
  - `command --help` for general help
  - `command subcommand --help` for specific help
- Use consistent terminology across commands, help, and output contracts.
- Prefer environment variables for configuration; support `.env` loading where appropriate.

## Structured Output Contract
- Maintain stable top-level fields: `success`, `message`/`error`, `details`, `data`.
- Treat structured outputs as API contracts for both humans and agents.
- Avoid silent contract changes; update docs and tests in the same change set.

## Documentation Sync Rules
- Put project/domain-specific command rules in project docs (design/requirements/spec files).
- When behavior or contracts change, update the relevant docs in the same change set.

## Security and Repo Hygiene
- Sanitize exported tooling artifacts before commit.
- If history contains secrets, treat history rewrite and force-push as required before public release.

## Logging
- Human-readable output goes to stdout.
- Errors and diagnostics go to stderr.
- Structured outputs (`--json`, `--yaml`, `--raw`) must be stdout-only.

## Exit Codes
Recommended Unix-style convention (adopt for new projects and when touching CLI error handling):
- 0: success
- 1: general error
- 2: invalid arguments or usage
- Avoid custom exit codes unless explicitly documented.

## Change Safety Checklist
Before finishing substantial changes:
1. Run syntax checks and `pytest` in the project environment.
2. Run at least one CLI smoke check for changed command paths.
3. Confirm new/changed functionality has corresponding test coverage.
4. Confirm help text reflects actual grammar.
5. Confirm docs and tests are aligned.
6. Confirm git status does not include sensitive artifacts.
