# Conductor Main

`conductor-main` is the interactive Gradio client for `conductor-core`. It turns
the reusable engine into a browser-based workflow for generating, auditioning,
visualizing, editing, and revisiting four-bar MIDI loops.

The app owns UI layout, callback adaptation, UI state, prompt editing, and its
Plotly piano roll. Core owns provider routing, generation, MIDI conversion,
audio helpers, and persisted artifacts. This separation allows another UI or
service to replace Conductor Main without rewriting the engine.

## Features

- Generate four-bar MIDI loops from a natural-language description.
- Switch among OpenAI, Anthropic, Google, and available Ollama models.
- Show model-specific temperature, reasoning toggle, and effort controls.
- Download generated MIDI and inspect it in an interactive piano roll.
- Render and replay audio with discovered SoundFonts.
- Change SoundFonts and re-render audio without another model call.
- Browse, load, refresh, and delete the 20 most recent generations.
- Edit the app-owned system-prompt override.
- Preserve provider messages, cost, model settings, and artifact metadata.
- Stop waiting for a long UI request without closing the application.

## Installation

Run these commands from the `conductor-main` project directory. Install a
compatible published `conductor-core` release first:

```
py -3.12 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .
conductor-main
```

The app opens at `http://127.0.0.1:7860/`.

## Provider setup

API keys can be entered in the app's **API Keys** accordion. Values entered
there are passed to Core for the current generation and are not written into
generation metadata.

Core also recognizes these environment variables:

```
$env:OPENAI_API_KEY = "..."
$env:GEMINI_API_KEY = "..."
$env:ANTHROPIC_API_KEY = "..."
$env:OLLAMA_API_HOST_ADDRESS = "http://localhost:11434"
```

Ollama appears as a provider only when its configured server is reachable and
reports installed models. Cloud provider usage may incur charges.

## Generate a loop

1. Open the **Text to MIDI** tab.
2. Choose the musical **Key** and **Scale**.
3. Describe the musical idea, instrumentation, rhythm, or mood.
4. Select a **Provider** and **Model**.
5. Adjust the model controls that are visible.
6. Select a SoundFont if audio playback is configured.
7. Click **Generate Loop**.

The completed view provides:

- **Download Generated MIDI** for importing the loop into a DAW;
- **Playback** when audio rendering succeeds;
- **MIDI Visualization**, an interactive four-bar piano roll;
- **Error Message** for provider, parsing, rendering, or configuration errors.

A useful description is specific without trying to reproduce the entire system
prompt. For example:

```text
warm neo-soul electric piano chords with syncopated upper extensions and a
simple bass movement
```

The selected key and scale are added to the request automatically.

## Model-specific controls

Conductor Main reads packaged model metadata and adapts its controls when the
provider or model changes:

| Control | Behavior |
|---|---|
| **Temperature** | Shown for models that accept sampling temperature |
| **Reasoning** | Toggle used by supported Anthropic and Google models |
| **Reasoning Effort** | Model-specific choices such as `minimal`, `low`, `high`, or `xhigh` |

Changing providers resets the model to a valid choice and refreshes dependent
controls. A hidden control is intentionally unavailable for that model rather
than missing from the installation.

Model labels show input and output prices per one million tokens when pricing
metadata is available. The saved generation records the provider-reported cost;
local Ollama generations normally have zero API cost.

## Data directory

Conductor applications share a single user-accessible data root. By default,
this client owns the `eval` directory below that root:

```text
~/.conductor/eval/
├── generations/       # retained MIDI, audio, messages, and metadata
├── Prompts/           # durable prompt overrides
└── soundfonts/         # user-supplied SoundFonts
```

On Windows, `~` normally expands to `%USERPROFILE%`, so the same location is
`%USERPROFILE%\.conductor\eval`. These files survive repository, virtual
environment, and package replacement. Back up `Prompts` and any user-supplied
SoundFonts as durable user data. Generation history is retained to the newest
20 items and can be deleted through the History sidebar when reclaiming space.

The location precedence is:

1. `CONDUCTOR_MAIN_DATA_DIR` selects this client's complete data directory;
2. `CONDUCTOR_HOME` selects the shared suite root and this client appends `eval`;
3. the default is `~/.conductor/eval`.

`CONDUCTOR_MAIN_SOUNDFONT_DIR` independently overrides only the directory used
for user-supplied SoundFonts. For example, PowerShell users can relocate the
whole suite or just this client:

```powershell
$env:CONDUCTOR_HOME = "D:\ConductorData"
$env:CONDUCTOR_MAIN_DATA_DIR = "D:\ConductorData\custom-eval"
$env:CONDUCTOR_MAIN_SOUNDFONT_DIR = "D:\Music\SoundFonts"
```

The most specific variable wins. Relative paths remain relative to the process
working directory, while paths beginning with `~` expand to the user home.

## SoundFonts and audio playback

The app discovers packaged and user-available `.sf2` SoundFonts through Core.
Core ships the default SoundFont; drop additional `.sf2` files into the
`~/.conductor/eval/soundfonts/` directory (or the directory set by
`CONDUCTOR_MAIN_SOUNDFONT_DIR`) to make them selectable. Audio rendering
requires all of the following:

1. the `conductor-core[playback]` dependencies;
2. FluidSynth installed and available on `PATH`;
3. FFmpeg installed and available on `PATH`;
4. an available SoundFont.

Use **Refresh SoundFonts** after adding a SoundFont while the app is running.
Select a different SoundFont and click **Re-render Audio** to audition the
current MIDI without regenerating it or making another provider call.

If the audio toolchain is unavailable, MIDI generation still succeeds. The app
shows the setup problem and leaves playback empty.

## History and generated files

Click **History** to open the recent-generation sidebar. From there you can:

- select and **Load** a previous generation;
- **Delete** a generation and its saved files;
- **Refresh** the list after external changes;
- inspect prompt, model, musical settings, time, and cost summaries.

By default, the app keeps the newest 20 generations under its data directory:

```text
~/.conductor/eval/generations/
└── gen_<id>/
    ├── loop.mid
    ├── loop.mp3        # when audio rendering succeeds
    ├── messages.json
    └── metadata.json
```

Loading history restores its MIDI, saved audio, visualization, generation ID,
and SoundFont metadata. If the previously used SoundFont is missing, the app
keeps the saved audio available and identifies the missing selection.

## Prompt Editor

The **Prompt Editor** tab displays the current loop-generation system prompt.
Saving creates or updates the app-owned override at
`~/.conductor/eval/Prompts/loop gen.txt`. The data-directory variables described
above relocate both this override and generation history.
Subsequent generations use that override instead of Core's packaged default.

The prompt defines the structured loop contract, timing conventions, and broad
musical guidance. Make targeted changes and keep the required output schema
intact; an invalid or ambiguous schema can cause provider parsing failures.

Deleting the override file returns the app to Core's packaged prompt.

### Migrating project-local data

Versions that defaulted to the repository directory do not move or delete that
data automatically. To keep using the old location temporarily, start the app
from the repository after setting:

```powershell
$env:CONDUCTOR_MAIN_DATA_DIR = $PWD
conductor-main
```

To adopt the new default, close the app and copy any existing `generations`,
`Prompts`, and `soundfonts` directories into
`%USERPROFILE%\.conductor\eval`. Review destination contents before copying so
that newer files are not overwritten. Once the migrated history and prompt load
correctly, the old ignored directories can be removed manually if desired.

## Stop Waiting behavior

Generation runs in a background thread so Gradio can continue yielding status
updates. **Stop Waiting** detaches the UI from the current wait, but it cannot
cancel a provider request already in flight. The provider may still finish and
incur cost after the UI stops displaying progress.

## Common problems

### The app starts but controls fail after recreating a venv

Confirm the launcher and interpreter come from the same environment:

```
python -c "import sys; print(sys.executable)"
where.exe conductor-main
```

Launch `.\.venv\Scripts\conductor-main.exe` explicitly. A globally installed
launcher can otherwise import a mixed set of dependencies and editable
packages.

### A provider is missing or generation reports an API-key error

- Install Core with the relevant provider extra or `providers`.
- Enter the key in the API Keys accordion or set the environment variable.
- For Ollama, confirm the server is running and has at least one model installed.

### MIDI works but playback is empty

Check FluidSynth, FFmpeg, and SoundFont availability. This is an optional audio
failure, not a failed MIDI generation.

### A saved history item names a missing SoundFont

Reinstall that SoundFont, refresh the list, or select another SoundFont and
re-render the saved MIDI.

## Development and validation

Install the development extra and run the client tests independently:

```
python -m pip install -e ".[dev]"
python -m pytest -q
```

The tests cover callback adaptation, model controls, SoundFont behavior,
history UI behavior and the package import boundary. They do not make live
provider calls or require the external audio
toolchain.

The package entry point is `conductor_main.app:main`. UI-specific visualization
lives in `conductor_main.visualization`; reusable generation behavior belongs
in `conductor-core`.

## Current limitations

- Loops are four bars in 4/4 at 120 BPM.
- Output quality varies by model, prompt, and generation settings.
- Cloud generation requires network access and may incur cost.
- Provider requests cannot currently be cancelled after dispatch.
- Playback requires external FluidSynth and FFmpeg installations.
