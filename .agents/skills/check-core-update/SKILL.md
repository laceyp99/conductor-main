---
name: check-core-update
description: Use when the user asks to check for a new conductor-core version, upgrade the conductor-core dependency, sync conductor-main with a new core release, or review core release notes/changelog. Triggers include "check version of core", "update conductor-core", or "did core release anything new".
---

# Check conductor-core for updates

## Context

`conductor-main` depends on `conductor-core` as a git dependency pinned to a
tag, e.g. in `pyproject.toml`:

```
"conductor-core[providers,playback] @ git+https://github.com/laceyp99/conductor-core.git@v0.5.1"
```

The pinned tag is the source of truth for what is currently depended on.

## Workflow

1. **Read the current pin.** Find the line mentioning `conductor-core` in `pyproject.toml`and note the pinned tag (e.g. `v0.5.1`).

2. **Discover the latest release.** Fetch the latest tag/release of `https://github.com/laceyp99/conductor-core` (use `gh release list` / `gh api repos/laceyp99/conductor-core/tags`, or `git ls-remote --tags`). If the latest tag equals the current pin, report "already up to date" and stop. Do not churn anything.

3. **If a newer tag exists, gather release notes and changelog.** Fetch:
    - GitHub release notes for every release between the pin and the latest tag (`gh release view <tag> -R laceyp99/conductor-core` for each).
    - The repo's `CHANGELOG.md` for those versions, if present. Pay special attention to: model metadata changes, provider routing, `LoopGenerationEngine` API changes, MIDI/storage/playback helper changes.

4. **Assess client impact.** Diff the pinned version of `conductor-main`-facing APIs against the new one. Concretely:
    - Check whether anything `src/conductor_main/` imports from core changed signature or moved.
    - Check whether model metadata the provider/model selectors rely on changed (see AGENTS.md: model selector smoke test).
    - Decide, per release note, whether conductor-main needs code changes, or whether the bump is drop-in. Nothing needs changing "just because".

5. **If an update is warranted** (user confirmed, or user asked for the update outright):
    - Bump the tag in `pyproject.toml` to the new version.
    - Make only the minimal conductor-main changes the notes require. Keep the package boundary intact: generation orchestration, provider routing, MIDI conversion, persistence, playback stay in core.
    - Run `ruff format` on the diff, then the full validation from README "Development and validation" (tests, ruff, import-without-launching check).

6. **Report back.** Summarize: current pin, latest version, what the release notes say, whether client changes were needed, what was changed, and validation results.

## Rules

- Never silently upgrade past breaking changes without explaining them.
- Do not make live provider calls while validating.
- Do not commit API keys or prompt/generation artifacts.