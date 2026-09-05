# Development & Troubleshooting

## Development and Validation

Sync the locked development environment and run the full local validation:

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev ruff check .
uv run --locked --extra dev pytest -q
uv build
```

The tests cover callback adaptation, model controls, SoundFont behavior, history UI behavior, and the package import boundary. They do not make live provider calls or require the external audio toolchain.

The package entry point is `conductor_main.app:main`. UI-specific visualization lives in `conductor_main.visualization`; reusable generation behavior belongs in `conductor-core`.

## Troubleshooting Common Problems

### A provider is missing or generation reports an API-key error

- Install Core with the relevant provider extra or `providers`.
- Enter the key in the API Keys accordion or set the environment variable.
- For Ollama, confirm the server is running and has at least one model installed.

### MIDI works but playback is empty

Check FluidSynth, FFmpeg, and SoundFont availability. This is an optional audio failure, not a failed MIDI generation.

### A saved history item names a missing SoundFont

Reinstall that SoundFont, refresh the list, or select another SoundFont and re-render the saved MIDI.

## Current Limitations

- Loops are four bars in 4/4 at 120 BPM.
- Output quality varies by model, prompt, and generation settings.
- Cloud generation requires network access and may incur cost.
- Provider requests cannot currently be cancelled after dispatch.
- Playback requires external FluidSynth and FFmpeg installations.
