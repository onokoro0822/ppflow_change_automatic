# Project working instructions

## Progress records

Before planning or changing this project, read these files in order:

1. `docs/progress/README.md`
2. `docs/progress/CURRENT.md`
3. `docs/progress/DECISIONS.md`
4. `docs/progress/NEXT.md`

For research direction or people-flow modelling work, also read:

- `docs/人流分析方針.md`
- `docs/progress/SOURCES.md`

Treat the progress records as the shared source of truth for the user and Codex. When they conflict with an older chat summary, source code comment, or draft README text, prefer the newest dated progress entry that cites a meeting record or explicit user decision.

After meaningful implementation, research, or direction changes:

1. Update `CURRENT.md` with the verified present state.
2. Update `NEXT.md` so its first item is the actual next priority.
3. Add confirmed decisions to `DECISIONS.md`; do not record assumptions as decisions.
4. Append a concise dated entry to `CHANGELOG.md`.
5. Add newly used papers, meeting notes, or specifications to `SOURCES.md`.

Use Asia/Tokyo dates. Keep completed work, pending work, and hypotheses clearly separated. Do not claim that an implementation or test is complete without verifying it.

## Weekly report

Also log meaningful work in the weekly report at `../weekly_reports/` (the newest `YYYYMMDD.md`, created from `TEMPLATE.md` if missing). See `../AGENTS.md` and `../weekly_reports/README.md` for the format and for how to build the weekly pptx.

## How to work (lessons from 2026-10)

These rules exist because skipping them caused real mistakes in this project.

- Before touching the Pseudo-PFLOW model, read `docs/progress/20261009_A方式_引き継ぎ.md` (current options, settings and pitfalls).
- Read the code that produces a number before interpreting it. For Pseudo-PFLOW, trace the Java (`../Pseudo-PFLOW-v3/src/pseudo/gen/ActGenerator.java` etc.) instead of guessing what a column or coefficient means. Confirm data definitions at the source (e.g. e-Stat definition tables) and write the source into the progress notes.
- Every number you report must come from a command you ran in this session. Quote the file or command it came from. If you did not run it, say so.
- When adding an option to Pseudo-PFLOW, keep the default identical to the old behaviour and prove it: rerun baseline and `diff -rq` against `.local/pflow/output/capacity_experiment/baseline/23`.
- `mvn -q compile` can finish without error while the classes contain "Unresolved compilation problems". Always check the run logs after a Java change.
- Keep CRLF line endings in the Java files that use them; check `git diff --stat` for unexpected whole-file diffs before committing.
- Distinguish what was fitted from what was predicted. A distribution that was used as a calibration target cannot be reported as validation.
- Report uncertainty plainly: single-seed results, 2% sample, hypotheses marked 【仮】.
- Prefer small, reviewable changes: one option or script per commit, with a test in `tests/` for Python code, and run `.venv/bin/python -m unittest discover -s tests` before committing.
- Long runs (about 8 minutes each) go in the background; record the exact command and scenario file so the result can be reproduced.
