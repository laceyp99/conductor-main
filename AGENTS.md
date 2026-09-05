# Conductor Main Agent Guide

Treat this file as good defaults rather than hard rules; the developer's stated
preferences in a session override anything here.

## Scope

This repository owns the replaceable Gradio client: layout, callbacks, UI
state, prompt editing, history presentation, and Plotly visualization.
Generation orchestration, provider routing, MIDI conversion, persistence, and
playback helpers belong to `conductor-core`.

## Who uses this

Solo musicians and hobbyist producers running the app locally to sketch loops
for a DAW. There is no hosted deployment and no multi-user data. Treat these as
high severity: losing or corrupting `~/.conductor/main` contents, leaking API
keys into logs or metadata, and silently spending provider credits.

## Intent

The client is deliberately replaceable. Every feature should still be
expressible against `conductor-core` alone; if a change only makes sense with
this UI, it probably belongs here, and if it would be useful to any other
client, it probably belongs in Core. See the README "Features" list for the
current feature set; do not add features that bypass Core's engine.

## Key paths

- `src/conductor_main/app.py`: Gradio layout and callback adaptation.
- `src/conductor_main/visualization.py`: UI-specific piano-roll rendering.
- `tests/test_app.py`: callback and UI behavior.
- `tests/test_package_boundary.py`: client isolation checks.

## Working rules

- Measure twice, cut once, and embody yagni principles.
- Keep callbacks thin and delegate generation to `LoopGenerationEngine`.
- Do not duplicate Core model metadata, provider routing, MIDI, or storage logic.
- Keep provider/model controls metadata-driven.
- Preserve optional audio behavior when FluidSynth or FFmpeg is unavailable.
- Importing the package must not launch Gradio.
- Do not make live provider calls during ordinary tests.
- Do not commit API keys, prompt experiments, generations, or build output.
- Keep relevant documentation and the changelog in sync with behavior changes.

## Stop hitting yourself

- Upgrading `conductor-core` usually changes model metadata; re-run the model
  selector smoke test rather than trusting green tests.
- Ruff line-length churn has caused rework before; run `ruff format` before
  reviewing a diff, not after.

## Validation

Run the full validation in README "Development and validation" before every
commit. Also smoke-test provider/model selectors and generation manually when
callback or layout behavior changes.

## Pull requests

- Use Conventional Commit prefixes (`feat`, `fix`, `chore`, `docs`, `ci`) with
  an optional scope such as `chore(deps)`.
- Keep PR titles short and lowercase; describe the user-visible effect.
- One concern per PR. Core version bumps get their own PR.
- Do not commit `plan.md`, `review.md`, or other workspace artifacts.

