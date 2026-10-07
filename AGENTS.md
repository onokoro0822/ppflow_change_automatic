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
