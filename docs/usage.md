# Usage Guide

## Generate a Loop

1. Open the **Text to MIDI** tab.
2. Choose the musical **Key** and **Scale**.
3. Describe the musical idea, instrumentation, rhythm, or mood.
4. Select a **Provider** and **Model**.
5. Adjust the model controls that are visible.
6. Select a SoundFont if audio playback is configured.
7. Click **Generate Loop**.

The completed view provides:

- **Download Generated MIDI**: Import the loop into a DAW.
- **Playback**: Play rendered audio when audio synthesis succeeds. Pausing rewinds to the start; scrub while paused to play from another point.
- **MIDI Visualization**: An interactive four-bar piano roll powered by Plotly.
- **Status**: Concise feedback on provider, parsing, rendering, or configuration issues.

A useful description is specific without trying to reproduce the entire system prompt. For example:

```text
warm neo-soul electric piano chords with syncopated upper extensions and a simple bass movement
```

The selected key and scale are added to the request automatically.

### What happens during generation

```mermaid
flowchart LR
    Input[Loop description and settings] --> UI[Conductor Main]
    UI --> Core[Conductor Core generation engine]
    Core --> Provider[Selected model provider]
    Provider --> Core
    Core --> Save[Save MIDI and generation metadata]
    Save --> Visualize[Build piano-roll visualization]
    Save --> Audio{Audio tools available?}
    Audio -->|Yes| Render[Render playback audio]
    Audio -->|No| MidiOnly[MIDI remains available]
    Visualize --> Results[Completed view]
    Render --> Results
    MidiOnly --> Results
```

Conductor Main collects the settings and presents the results, while Core owns
provider communication, structured response parsing, MIDI creation, persistence,
and optional audio rendering.

## Model-Specific Controls

Conductor Main reads Core's model metadata and adapts its controls when the provider or model changes. Ollama models use the capabilities Core reports for the selected model.

| Control | Behavior |
|---|---|
| **Temperature** | Shown for models that accept sampling temperature. Models that require a fixed temperature while reasoning show that value, locked, while reasoning is on |
| **Reasoning** | On/off toggle for models without effort levels that can turn reasoning off |
| **Reasoning Effort** | Model-specific choices such as `none`, `minimal`, `low`, `high`, or `xhigh`. The selected level is always sent. `none` appears for every model that can turn reasoning off; otherwise the lowest level is the least reasoning the model allows |
| **Ollama Context Size** | Under **Advanced Settings**, shown only for Ollama. Choose 1,024 to 262,144 tokens, or keep **Ollama default**. The selection is kept when you switch providers but is only sent to Ollama. Larger windows use more memory |

Models that always reason and offer no effort levels show no reasoning control.

Changing providers resets the model to a valid choice and refreshes dependent controls. Your temperature carries over between models, the reasoning effort carries over when the new model offers that level, and the reasoning toggle carries over between models that have one. A hidden control is intentionally unavailable for that model rather than missing from the installation.

Model labels show input and output prices per one million tokens when pricing metadata is available. The saved generation records the provider-reported cost; local Ollama generations normally have zero API cost.

## Prompt Editor

The **Prompt Editor** tab displays the current loop-generation system prompt. Saving creates or updates the app-owned override at `~/.conductor/main/Prompts/loop gen.txt`. Subsequent generations use that override instead of Core's packaged default.

The prompt defines the structured loop contract, timing conventions, and broad musical guidance. Make targeted changes and keep the required output schema intact; an invalid or ambiguous schema can cause provider parsing failures.

Deleting the override file returns the app to Core's packaged prompt.

## Stop Waiting Behavior

Generation runs in a background thread so Gradio can continue yielding status updates. **Stop Waiting** detaches the UI from the current wait, but it cannot cancel a provider request already in flight. The provider may still finish and incur cost after the UI stops displaying progress.

```mermaid
flowchart TD
    Start[Generate Loop] --> Request[Provider request begins]
    Request --> Waiting{Still waiting?}
    Waiting -->|Provider finishes| Results[Show generated results]
    Waiting -->|Stop Waiting clicked| Detached[UI stops showing progress]
    Detached --> InFlight[Provider request may remain in flight]
    InFlight --> Cost[Request may finish and incur cost]
```
