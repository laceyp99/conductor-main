"""
This file is using Gradio for the Conductor application. It makes the generation progress more user friendly by providing a GUI for the user to interact with.

Features:
- Text to MIDI generation with multiple AI providers
- Audio playback of generated MIDI using FluidSynth
- Session history with persistent storage (up to 20 generations)
- Toggleable history sidebar panel
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue

import gradio as gr
from conductor_core import (
    AudioRenderingError,
    EngineConfig,
    GenerationRequest,
    LoopGenerationEngine,
    ProviderCredentials,
)
from conductor_core.music import ENHARMONIC_NOTE_NAMES, get_loop_prompt, get_model_info
from conductor_core.playback import (
    add_soundfont_search_dir,
    get_default_soundfont,
    get_playback_status_message,
    is_playback_available,
    list_soundfonts,
    midi_to_mp3,
)
from conductor_core.providers import ollama as ollama_api
from conductor_core.storage import FilesystemArtifactStore
from mido import MidiFile

from conductor_main.visualization import visualize_midi_plotly

DEFAULT_PROVIDER = "Google"
DEFAULT_TEMPERATURE = 0.1
CONDUCTOR_APP_DIRNAME = "main"
MAX_HISTORY_GENERATIONS = 20
# 0 sends no num_ctx, so Ollama's own default applies.
OLLAMA_CONTEXT_SIZE_CHOICES = [("Ollama default", 0)] + [
    (f"{size:,}", size) for size in (1024, 4096, 16384, 65536, 262144)
]
KEY_CHOICES = (
    "C",
    "C#/Db",
    "D",
    "D#/Eb",
    "E",
    "F",
    "F#/Gb",
    "G",
    "G#/Ab",
    "A",
    "A#/Bb",
    "B",
)
SHARP_KEY_ALIASES = {
    "C#/Db": "C#",
    "D#/Eb": "D#",
    "F#/Gb": "F#",
    "G#/Ab": "G#",
    "A#/Bb": "A#",
}
CORE_KEY_TO_UI = {
    key: KEY_CHOICES[pitch_class]
    for pitch_class, keys in enumerate(ENHARMONIC_NOTE_NAMES)
    for key in keys
}
APP_CSS = """
.center-title { text-align: center; font-size: 3em; }
.app-header {
    position: relative;
}
.app-header .history-toggle {
    position: absolute;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    z-index: 1;
}
.history-sidebar {
    background: var(--background-fill-primary);
    border-left: 1px solid var(--border-color-primary);
    color: var(--body-text-color);
    height: 100%;
    overflow-y: auto;
}
.history-list label {
    border: 1px solid var(--border-color-primary);
    border-radius: 8px;
    margin-bottom: 8px;
    padding: 10px;
}
.history-list label:hover {
    border-color: var(--border-color-accent);
}
.history-list label:has(input:checked) {
    border-color: var(--border-color-accent);
    background: var(--block-background-fill);
}
"""


def _resolve_conductor_home() -> Path:
    """Resolve the shared root for mutable Conductor suite data."""
    conductor_home = os.environ.get("CONDUCTOR_HOME")
    if conductor_home:
        return Path(conductor_home).expanduser()

    return Path.home() / ".conductor"


def _resolve_app_data_dir() -> Path:
    """Resolve the mutable data directory owned by this client."""
    app_data_dir = os.environ.get("CONDUCTOR_MAIN_DATA_DIR")
    if app_data_dir:
        return Path(app_data_dir).expanduser()

    return _resolve_conductor_home() / CONDUCTOR_APP_DIRNAME


def _resolve_app_soundfont_dir() -> Path:
    """Resolve the directory for user-supplied SoundFonts."""
    soundfont_dir = os.environ.get("CONDUCTOR_MAIN_SOUNDFONT_DIR")
    if soundfont_dir:
        return Path(soundfont_dir).expanduser()

    return _resolve_app_data_dir() / "soundfonts"


APP_DATA_DIR = _resolve_app_data_dir()
APP_SOUNDFONT_DIR = _resolve_app_soundfont_dir()
PROMPT_OVERRIDE_PATH = APP_DATA_DIR / "Prompts" / "loop gen.txt"

add_soundfont_search_dir(APP_SOUNDFONT_DIR)
HISTORY_STORE = FilesystemArtifactStore(
    APP_DATA_DIR / "generations",
    max_generations=MAX_HISTORY_GENERATIONS,
)


def load_history():
    return HISTORY_STORE.load_history()


def normalize_key_for_core(key):
    """Translate combined black-key UI labels to Core's sharp spelling."""
    return SHARP_KEY_ALIASES.get(key, key)


def normalize_key_for_ui(key):
    """Coerce a supported Core key spelling to its 12-note UI choice."""
    if not isinstance(key, str):
        return None

    return CORE_KEY_TO_UI.get(normalize_key_for_core(key))


def get_generation(gen_id):
    return HISTORY_STORE.get_generation(gen_id)


def delete_generation(gen_id):
    return HISTORY_STORE.delete_generation(gen_id)


def update_generation_audio(gen_id, audio_path, soundfont=None):
    return HISTORY_STORE.update_generation_audio(
        gen_id, audio_path, soundfont=soundfont
    )


def format_price_summary(price_value):
    """Format scalar or tiered pricing for dropdown labels."""
    if isinstance(price_value, (int, float)):
        return f"${price_value:.2f}"

    if isinstance(price_value, dict):
        numeric_values = [
            value for value in price_value.values() if isinstance(value, (int, float))
        ]
        if not numeric_values:
            return None

        min_price = min(numeric_values)
        max_price = max(numeric_values)
        if min_price == max_price:
            return f"${min_price:.2f}"
        return f"${min_price:.2f}-${max_price:.2f}"

    return None


def format_model_label(provider, model_name):
    """Build the model dropdown label with pricing when available."""
    if provider == "Ollama":
        return model_name

    model_info = get_model_info()
    provider_models = model_info["models"].get(provider, {})
    model_data = provider_models.get(model_name, {})
    cost = model_data.get("cost")

    if not cost or "input" not in cost or "output" not in cost:
        return model_name

    input_price = format_price_summary(cost["input"])
    output_price = format_price_summary(cost["output"])

    if not input_price or not output_price:
        return model_name

    return f"{model_name} ({input_price} in / {output_price} out per 1M tokens)"


def get_model_dropdown_choices(provider):
    """Get dropdown choices as (label, value) tuples for a provider."""
    models = get_models_for_provider(provider)
    return [
        (format_model_label(provider, model_name), model_name) for model_name in models
    ]


@dataclass(frozen=True)
class HistoryControlUpdates:
    """Coherent form updates derived from one saved generation."""

    key: object
    scale: object
    description: object
    provider: object
    model: object
    temperature: object
    use_thinking: object
    effort: object
    requested_temperature: object
    reasoning_control: object
    warnings: tuple[str, ...]

    def as_tuple(self):
        """Return updates in the order used by the history-load callback."""
        return (
            self.key,
            self.scale,
            self.description,
            self.provider,
            self.model,
            self.temperature,
            self.use_thinking,
            self.effort,
            self.requested_temperature,
            self.reasoning_control,
        )


def get_history_control_updates(gen):
    """Build form updates, checking saved Ollama models against local discovery."""
    model_info = get_model_info()
    known_models = model_info["models"]
    provider = gen.provider
    model = gen.model
    warnings = []
    ui_key = normalize_key_for_ui(gen.key)
    if ui_key is None:
        key_update = gr.update()
        warnings.append(f"Unavailable key: {gen.key!r}.")
    else:
        key_update = gr.update(value=ui_key)

    provider_choices = list(known_models)
    ollama_status = (
        ollama_api.get_ollama_status(request_timeout=3.0)
        if provider == "Ollama"
        else None
    )
    if ollama_status and ollama_status["available"]:
        provider_choices.append("Ollama")
    provider_is_available = provider in known_models or bool(
        ollama_status and ollama_status["available"]
    )
    if not provider_is_available:
        provider_choices.append((f"{provider} (unavailable)", provider))

    if provider_is_available:
        provider_models = (
            ollama_status["models"] if provider == "Ollama" else known_models[provider]
        )
        model_choices = [
            (format_model_label(provider, model_name), model_name)
            for model_name in provider_models
        ]
        model_is_available = model in provider_models
    else:
        model_choices = []
        model_is_available = False

    if not model_is_available:
        model_choices.append((f"{model} (unavailable)", model))
        warnings.append(f"Unavailable selection: {provider} / {model}.")

    saved_thinking = getattr(gen, "use_thinking", None)
    saved_effort = getattr(gen, "effort", None)
    reasoning_was_recorded = saved_thinking is not None and saved_effort is not None

    if model_is_available:
        # A saved use_thinking=False on an effort model selects its off level.
        settings = get_model_settings(
            provider,
            model,
            bool(saved_thinking),
            saved_effort if saved_thinking else None,
        )
    else:
        settings = {
            "reasoning_control": None,
            "show_temperature": True,
            "temperature_value": gen.temperature,
            "temperature_interactive": True,
            "show_thinking": True,
            "thinking_value": False,
            "effort_options": [],
            "effort_value": "low",
            "show_effort": True,
        }

    if not reasoning_was_recorded:
        warnings.append("Reasoning settings weren't saved; defaults applied.")

    effort_value = saved_effort if reasoning_was_recorded else settings["effort_value"]
    if model_is_available:
        # Known models derive use_thinking from their reasoning control.
        thinking_value = settings["thinking_value"]
        if settings["show_effort"]:
            effort_value = settings["effort_value"]
    else:
        thinking_value = saved_thinking if reasoning_was_recorded else False
    effort_options = list(settings["effort_options"])
    if effort_value not in effort_options:
        effort_options.append(effort_value)

    return HistoryControlUpdates(
        key=key_update,
        scale=gr.update(value=gen.scale),
        description=gr.update(value=gen.prompt),
        provider=gr.update(choices=provider_choices, value=provider),
        model=gr.update(choices=model_choices, value=model),
        temperature=gr.update(
            visible=get_temperature_visibility(settings["show_temperature"]),
            value=gen.temperature
            if settings["temperature_interactive"]
            else settings["temperature_value"],
            interactive=settings["temperature_interactive"],
        ),
        use_thinking=gr.update(
            visible=settings["show_thinking"],
            value=thinking_value,
        ),
        effort=gr.update(
            choices=effort_options or None,
            value=effort_value,
            visible=settings["show_effort"],
        ),
        # A locked slider shows the fixed value, so keep the prior choice.
        requested_temperature=gen.temperature
        if settings["temperature_interactive"]
        else gr.update(),
        reasoning_control=settings["reasoning_control"],
        warnings=tuple(warnings),
    )


def get_providers():
    """Get list of available providers including Ollama if models are available.

    Returns:
        list: List of provider names.
    """
    model_info = get_model_info()
    providers = list(model_info["models"].keys())
    # get_model_list() lists names without inspecting every installed model.
    if ollama_api.get_model_list():
        providers.append("Ollama")
    return providers


def get_models_for_provider(provider):
    """Get list of models for a specific provider.

    Args:
        provider (str): The provider name.

    Returns:
        list: List of model names for the provider.
    """
    if provider == "Ollama":
        return ollama_api.get_model_list()
    model_info = get_model_info()
    if provider in model_info["models"]:
        return list(model_info["models"][provider].keys())
    return []


def get_selected_model(provider, model_choice):
    """Normalize the selected model for a provider."""
    models = get_models_for_provider(provider)
    if model_choice in models:
        return model_choice
    return models[0] if models else None


def get_model_config(provider, model):
    """Return Core's reasoning and temperature metadata for one model."""
    if provider == "Ollama":
        return ollama_api.get_model_status(model)["model_capabilities"] or {}
    return get_model_info()["models"].get(provider, {}).get(model, {})


def get_effort_choices(model_config):
    """Return effort levels, adding "none" when Core can switch thinking off."""
    effort_options = list(model_config.get("effort_options") or [])
    if (
        effort_options
        and model_config.get("thinking_off") == "disabled"
        and "none" not in effort_options
    ):
        # "none" maps to use_thinking=False, Core's official thinking-off setting.
        effort_options.insert(0, "none")
    return effort_options


def get_temperature_visibility(show_temperature):
    """Keep a hidden temperature slider mounted.

    Gradio only paints the slider's fill when its value changes, so a slider
    re-created by visible=False shows an unfilled track until it is moved.
    """
    return True if show_temperature else "hidden"


def get_reasoning_control(model_config):
    """Pick the reasoning control Core's metadata calls for.

    Returns "effort" for models with effort levels ("none", or else the lowest
    level, is the off setting), "toggle" for models that can switch thinking off,
    "always_on" for models that always reason without selectable levels, and
    "none" for models without extended thinking.
    """
    if not model_config.get("extended_thinking"):
        return "none"
    if model_config.get("effort_options"):
        return "effort"
    if model_config.get("thinking_off", "disabled") == "disabled":
        return "toggle"
    return "always_on"


def get_model_settings(
    provider,
    model_choice,
    use_thinking=False,
    effort=None,
    requested_temperature=None,
):
    """Resolve the provider/model UI settings for dependent controls.

    The requested effort and temperature carry over when the selected model
    supports them; a fixed thinking temperature only overrides what is shown.
    """
    selected_model = get_selected_model(provider, model_choice)
    model_config = get_model_config(provider, selected_model) if selected_model else {}
    reasoning_control = get_reasoning_control(model_config)
    effort_options = get_effort_choices(model_config)
    if effort in effort_options:
        effort_value = effort
    else:
        effort_value = effort_options[0] if effort_options else "low"
    adds_none = "none" in effort_options and "none" not in (
        model_config.get("effort_options") or []
    )

    # Effort models send use_thinking=True with the selected level, including
    # a provider's own "none"; only the "none" Main adds sends use_thinking=False.
    if reasoning_control == "toggle":
        thinking_value = bool(use_thinking)
    elif reasoning_control == "effort":
        thinking_value = not (adds_none and effort_value == "none")
    else:
        thinking_value = reasoning_control == "always_on"

    fixed_temperature = model_config.get("thinking_fixed_temperature")
    temperature_is_fixed = thinking_value and fixed_temperature is not None
    if requested_temperature is None:
        requested_temperature = DEFAULT_TEMPERATURE

    return {
        "selected_model": selected_model,
        "reasoning_control": reasoning_control,
        "show_temperature": model_config.get("temperature_supported", True),
        "temperature_value": fixed_temperature
        if temperature_is_fixed
        else requested_temperature,
        "temperature_interactive": not temperature_is_fixed,
        "show_thinking": reasoning_control == "toggle",
        "thinking_value": thinking_value,
        "effort_options": effort_options,
        "effort_value": effort_value,
        "show_effort": reasoning_control == "effort",
    }


def sync_model_capabilities(
    provider,
    model_choice,
    use_thinking=False,
    effort=None,
    requested_temperature=None,
    previous_control=None,
):
    """Synchronize model selection and dependent controls from one explicit code path.

    The toggle state carries over only between toggle models; the hidden
    checkbox of other controls does not hold a user choice.
    """
    choices = get_model_dropdown_choices(provider)
    settings = get_model_settings(
        provider,
        model_choice,
        bool(use_thinking) and previous_control == "toggle",
        effort,
        requested_temperature,
    )

    return (
        gr.update(choices=choices, value=settings["selected_model"]),
        gr.update(
            visible=get_temperature_visibility(settings["show_temperature"]),
            value=settings["temperature_value"],
            interactive=settings["temperature_interactive"],
        ),
        gr.update(
            visible=settings["show_thinking"],
            value=settings["thinking_value"],
        ),
        gr.update(
            choices=settings["effort_options"] or None,
            value=settings["effort_value"],
            visible=settings["show_effort"],
        ),
        settings["reasoning_control"],
    )


def sync_controls_for_provider(provider, *control_values):
    """Refresh dependent controls when the provider changes.

    The model dropdown is not an input: Gradio rejects its previous value
    once its choices belong to another provider, so the first model is used.
    """
    return sync_model_capabilities(provider, None, *control_values)


def sync_controls_for_model(*control_values):
    """Refresh dependent controls when the selected model changes."""
    return sync_model_capabilities(*control_values)


def sync_controls_for_thinking(*control_values):
    """Refresh dependent controls when the reasoning toggle changes."""
    return sync_model_capabilities(*control_values)


def sync_controls_for_effort(*control_values):
    """Refresh dependent controls when the reasoning effort changes."""
    return sync_model_capabilities(*control_values)


def sync_context_size_for_provider(provider):
    """Show Ollama's advanced settings only for Ollama; keep the selection."""
    return gr.update(visible=provider == "Ollama")


def get_soundfont_choices():
    """Get the available SoundFont filenames for the UI."""
    return list_soundfonts()


def get_selected_soundfont(soundfont_choice=None):
    """Normalize the selected SoundFont for the UI."""
    soundfonts = get_soundfont_choices()
    if not soundfonts:
        return None

    if soundfont_choice:
        requested_name = Path(soundfont_choice).name
        if requested_name in soundfonts:
            return requested_name

    default_soundfont = get_default_soundfont()
    if default_soundfont:
        default_soundfont_name = Path(default_soundfont).name
        if default_soundfont_name in soundfonts:
            return default_soundfont_name

    return soundfonts[0]


def get_soundfont_dropdown_update(soundfont_choice=None):
    """Build a dropdown update for the current SoundFont selection."""
    return gr.update(
        choices=get_soundfont_choices(),
        value=get_selected_soundfont(soundfont_choice),
    )


def has_active_rerender_target(midi_path):
    """Return whether the UI currently has a MIDI file available to rerender."""
    return bool(midi_path and Path(midi_path).exists())


def rerender_available(soundfont_choice=None, midi_path=None):
    """Return whether rerendering should be enabled for the current UI state."""
    selected_soundfont = get_selected_soundfont(soundfont_choice)
    playback_available, _ = is_playback_available(selected_soundfont)
    return (
        playback_available
        and selected_soundfont is not None
        and has_active_rerender_target(midi_path)
    )


def get_rerender_button_update(soundfont_choice=None, midi_path=None):
    """Build a button update for the current rerender availability."""
    return gr.update(interactive=rerender_available(soundfont_choice, midi_path))


def get_soundfont_status_message(soundfont_choice=None):
    """Build the status text for the current SoundFont and playback state."""
    selected_soundfont = get_selected_soundfont(soundfont_choice)
    playback_available, _ = is_playback_available(selected_soundfont)

    if playback_available and selected_soundfont:
        return f"Found {len(get_soundfont_choices())} SoundFonts. Selected {selected_soundfont}."

    return get_playback_status_message(selected_soundfont)


def refresh_soundfont_controls(soundfont_choice=None, midi_path=None):
    """Refresh SoundFont UI controls from the filesystem."""
    return (
        get_soundfont_dropdown_update(soundfont_choice),
        get_rerender_button_update(soundfont_choice, midi_path),
        get_soundfont_status_message(soundfont_choice),
    )


def rerender_current_audio(
    midi_path,
    soundfont_choice,
    saved_soundfont,
    generation_id,
    current_audio_path,
):
    """Re-render the current MIDI file with the selected SoundFont on demand."""
    if not midi_path:
        return (
            current_audio_path,
            "No MIDI file available to re-render.",
            saved_soundfont,
            current_audio_path,
        )

    if not Path(midi_path).exists():
        return (
            current_audio_path,
            f"MIDI file not found: {midi_path}",
            saved_soundfont,
            current_audio_path,
        )

    selected_soundfont = get_selected_soundfont(soundfont_choice)
    if not selected_soundfont:
        return (
            current_audio_path,
            get_playback_status_message(soundfont_choice),
            saved_soundfont,
            current_audio_path,
        )

    if (
        saved_soundfont == selected_soundfont
        and current_audio_path
        and Path(current_audio_path).exists()
    ):
        return (
            current_audio_path,
            f"Audio already rendered with {selected_soundfont}.",
            saved_soundfont,
            current_audio_path,
        )

    output_path = current_audio_path or str(Path(midi_path).with_suffix(".mp3"))
    try:
        rendered_audio_path = midi_to_mp3(
            midi_path,
            output_path=output_path,
            soundfont_name=selected_soundfont,
        )
    except AudioRenderingError as exc:
        return (
            current_audio_path,
            str(exc),
            saved_soundfont,
            current_audio_path,
        )

    persisted_audio_path = rendered_audio_path
    if generation_id:
        updated_generation = update_generation_audio(
            generation_id,
            rendered_audio_path,
            soundfont=selected_soundfont,
        )
        if updated_generation and updated_generation.audio_path:
            persisted_audio_path = updated_generation.audio_path

    return (
        persisted_audio_path,
        f"Rendered audio with {selected_soundfont}.",
        selected_soundfont,
        persisted_audio_path,
    )


def load_app_prompt_override():
    """Load the app-owned prompt override if the user has saved one."""
    if not PROMPT_OVERRIDE_PATH.exists():
        return None

    with PROMPT_OVERRIDE_PATH.open("r", encoding="utf-8") as prompt_file:
        return prompt_file.read()


def get_prompt_editor_text():
    """Return the prompt text shown in the Prompt Editor."""
    return load_app_prompt_override() or get_loop_prompt()


def save_prompts(loop_gen_text):
    """This function saves any changes to the loop generation prompt to the text file.

    Args:
        loop_gen_text (str): The loop generation prompt text.

    Returns:
        str: A message indicating the status of the save operation.
    """
    PROMPT_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROMPT_OVERRIDE_PATH.open("w", encoding="utf-8") as f:
        f.write(loop_gen_text)
    return (
        "Prompts saved successfully at "
        + datetime.now(timezone.utc).astimezone().strftime("%I:%M:%S %p on %B %d, %Y")
        + "."
    )


def run_loop(
    key,
    scale,
    description,
    temp,
    model_choice,
    use_thinking,
    effort,
    soundfont_choice,
    openai_key,
    gemini_key,
    claude_key,
    ollama_num_ctx=None,
    provider=None,
):
    """Run the loop generation process based on user inputs and selected model.

    This is a generator function that yields progress updates while the API call runs
    in a background thread. Gradio can stop waiting for the generator, but that does
    not cancel the in-flight provider request.

    Args:
        key (str): The key for the loop that the user selects from the dropdown.
        scale (str): The scale for the loop that the user selects from the Major/minor dropdown.
        description (str): A description of the loop that the user input in the text box.
        temp (float): The sampling temperature for the model that the user selects from the slider.
        model_choice (str): The model that the user selects from the dropdown.
        use_thinking (bool): Whether to enable extended thinking for supported models.
        effort (str): The reasoning effort level for models with effort options.
         soundfont_choice (str): The selected SoundFont filename for audio rendering.
        openai_key (str): The OpenAI API key that the user inputs in the text box.
        gemini_key (str): The Gemini API key that the user inputs in the text box.
        claude_key (str): The Claude API key that the user inputs in the text box.
        ollama_num_ctx (int | None): Optional Ollama context window size; 0 or None
            keeps Ollama's default. Sent only when provider is Ollama.
        provider (str | None): The selected provider.

    Yields:
         tuple: (file_path, audio_path, visualization, status_message, stop_button_update,
             generation_id, saved_soundfont, current_audio_path) - intermediate yields
             show progress and keep the stop-waiting control visible, final yield contains the generated MIDI,
             audio, and persisted audio metadata for rerendering.
    """
    try:
        selected_soundfont = get_selected_soundfont(soundfont_choice)
        credentials = ProviderCredentials(
            openai_api_key=openai_key.strip()
            if openai_key and openai_key.strip()
            else None,
            google_api_key=gemini_key.strip()
            if gemini_key and gemini_key.strip()
            else None,
            anthropic_api_key=claude_key.strip()
            if claude_key and claude_key.strip()
            else None,
        )
        engine = LoopGenerationEngine(
            EngineConfig.from_defaults(
                artifact_root=HISTORY_STORE.artifact_root,
                provider_credentials=credentials,
                prompt_override=load_app_prompt_override(),
                default_soundfont_path=selected_soundfont,
                max_generations=MAX_HISTORY_GENERATIONS,
            )
        )
        request = GenerationRequest(
            key=normalize_key_for_core(key),
            scale=scale,
            description=description,
            model=model_choice,
            temperature=temp,
            use_thinking=use_thinking,
            effort=effort,
            render_audio=True,
            soundfont_path=selected_soundfont,
            ollama_num_ctx=int(ollama_num_ctx)
            if provider == "Ollama" and ollama_num_ctx
            else None,
        )
        progress_events = Queue()

        def handle_progress(event):
            progress_events.put(event)

        # Yield initial status and show the stop-waiting button.
        yield (
            None,
            None,
            None,
            "Working on it...",
            gr.update(visible=True),
            None,
            None,
            None,
        )

        # Run the synchronous Core engine in a background thread so the UI can stop waiting.
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            engine.generate,
            request,
            handle_progress,
        )
        try:
            # Poll for completion, yielding periodically so Gradio can interrupt the wait.
            while not future.done():
                try:
                    progress_event = progress_events.get(timeout=0.5)
                    status_message = progress_event.message
                except Empty:
                    status_message = "Generating MIDI..."
                yield (
                    None,
                    None,
                    None,
                    status_message,
                    gr.update(visible=True),
                    None,
                    None,
                    None,
                )

            while not progress_events.empty():
                progress_event = progress_events.get()
                yield (
                    None,
                    None,
                    None,
                    progress_event.message,
                    gr.update(visible=True),
                    None,
                    None,
                    None,
                )

            # Get the result (will raise exception if the API call failed)
            result = future.result()
        finally:
            # Generator cancellation raises GeneratorExit at a yield. Do not wait for an
            # in-flight provider call here: Stop Waiting only detaches the UI and the
            # provider request may still complete and incur cost in the background.
            executor.shutdown(wait=False)

        print(f"Total cost: {result.cost}")
        visualization = visualize_midi_plotly(MidiFile(result.midi_path))

        # Final yield with the completed result and hide the stop-waiting button.
        yield (
            result.midi_path,
            result.audio_path,
            visualization,
            "\n".join(result.warnings),
            gr.update(visible=False),
            result.generation_id,
            result.metadata.soundfont,
            result.audio_path,
        )

    except Exception as e:
        # Catch any exception and yield the error message, hide the stop-waiting button.
        yield None, None, None, str(e), gr.update(visible=False), None, None, None


def toggle_history_sidebar(is_visible, selected_id):
    """Toggle the history sidebar while preserving a valid selection.

    Hiding leaves the list untouched; showing reloads it and keeps the
    selection while its generation still exists. Either way a pending delete
    confirmation is cancelled.
    """
    new_visible = not is_visible
    button_text = "Hide History" if new_visible else "History"
    return (
        new_visible,
        button_text,
        gr.update(visible=new_visible),
        refresh_history(selected_id) if new_visible else gr.update(),
        *hide_delete_confirmation(),
    )


def format_history_reasoning(gen, model_info):
    """Format persisted reasoning metadata for a history card."""
    use_thinking = getattr(gen, "use_thinking", None)
    effort = getattr(gen, "effort", None)
    if use_thinking is None or effort is None:
        return ""

    provider = getattr(gen, "provider", None)
    model_config = model_info["models"].get(provider, {}).get(gen.model, {})
    reasoning_control = get_reasoning_control(model_config)

    if reasoning_control == "effort":
        # use_thinking=False is "none", or the lowest level Core sends instead.
        return effort if use_thinking else get_effort_choices(model_config)[0]
    if reasoning_control != "none":
        return "reasoning" if use_thinking else ""
    if use_thinking:
        return "reasoning"
    if effort != "low":
        return effort
    return ""


def get_history_choices():
    """Return newest-first, identifiable entries for the single history selector."""
    history = load_history()
    model_info = get_model_info() if history else None
    choices = []
    for gen in history:
        timestamp = gen.timestamp.strftime("%b %d, %I:%M %p")
        prompt = gen.prompt[:60] + "..." if len(gen.prompt) > 60 else gen.prompt
        reasoning = format_history_reasoning(gen, model_info)
        model = f"{gen.model} ({reasoning})" if reasoning else gen.model
        label = f"{gen.key} {gen.scale} | {prompt} | {model} | {timestamp}"
        choices.append((label, gen.id))
    return choices


def select_history_item(gen_id):
    """Clear an old loaded indicator when the selected entry changes."""
    return "Select Load to restore this generation." if gen_id else ""


def loaded_history_status(gen_id):
    return "Loaded generation." if gen_id else "Generation was not loaded."


def show_delete_confirmation(gen_id):
    """Require a separate confirmation before deleting a selected generation."""
    if not gen_id:
        return (
            gr.update(visible=True),
            gr.update(visible=False),
            "Select a generation to delete.",
        )
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        "Confirm deletion of the selected generation.",
    )


def hide_delete_confirmation():
    return gr.update(visible=True), gr.update(visible=False)


def cancel_delete_confirmation():
    return gr.update(visible=True), gr.update(visible=False), ""


def load_history_item(gen_id):
    """Load a history item into the main view.

    Args:
        gen_id (str): The generation ID to load.

    Returns:
        tuple: (midi_path, audio_path, soundfont_update, visualization, status_message,
               generation_id, saved_soundfont, current_audio_path, rerender_update,
               key_update, scale_update, description_update, provider_update, model_update,
               temperature_update, thinking_update, effort_update,
               requested_temperature, reasoning_control)
    """
    unchanged_controls = tuple(gr.update() for _ in range(10))
    if not gen_id:
        return (
            None,
            None,
            get_soundfont_dropdown_update(),
            None,
            "No generation selected",
            None,
            None,
            None,
            get_rerender_button_update(),
            *unchanged_controls,
        )

    gen = get_generation(gen_id)
    if not gen:
        return (
            None,
            None,
            get_soundfont_dropdown_update(),
            None,
            f"Generation {gen_id} not found",
            None,
            None,
            None,
            get_rerender_button_update(),
            *unchanged_controls,
        )

    # Check if files exist
    if not Path(gen.midi_path).exists():
        return (
            None,
            None,
            get_soundfont_dropdown_update(gen.soundfont),
            None,
            f"MIDI file not found: {gen.midi_path}",
            None,
            None,
            None,
            get_rerender_button_update(gen.soundfont, None),
            *unchanged_controls,
        )

    warnings = []
    if gen.soundfont:
        saved_soundfont_name = Path(gen.soundfont).name
        if saved_soundfont_name not in get_soundfont_choices():
            warnings.append(f"Missing SoundFont: {saved_soundfont_name}.")

    control_updates = get_history_control_updates(gen)
    warnings.extend(control_updates.warnings)

    # Load visualization
    try:
        midi = MidiFile(gen.midi_path)
        visualization = visualize_midi_plotly(midi)
    except Exception:
        visualization = None

    # Get audio path if it exists
    audio_path = (
        gen.audio_path if gen.audio_path and Path(gen.audio_path).exists() else None
    )

    return (
        gen.midi_path,
        audio_path,
        get_soundfont_dropdown_update(gen.soundfont),
        visualization,
        " ".join(warnings),
        gen.id,
        gen.soundfont,
        audio_path,
        get_rerender_button_update(gen.soundfont, gen.midi_path),
        *control_updates.as_tuple(),
    )


def delete_history_item(
    gen_id,
    current_generation_id=None,
    soundfont_choice=None,
    midi_path=None,
    current_saved_soundfont=None,
    current_audio_path=None,
):
    """Delete a confirmed item and clear loaded artifacts only if it was active."""
    if not gen_id:
        return (
            gr.update(choices=get_history_choices(), value=None),
            "No generation selected",
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(),
            gr.update(),
            gr.update(),
            current_generation_id,
            current_saved_soundfont,
            current_audio_path,
            get_rerender_button_update(soundfont_choice, midi_path),
        )

    success = delete_generation(gen_id)
    choices = get_history_choices()
    deleted_active = success and gen_id == current_generation_id
    return (
        gr.update(choices=choices, value=None if success else gen_id),
        "Deleted generation" if success else "Failed to delete generation",
        gr.update(visible=True),
        gr.update(visible=False),
        None if deleted_active else gr.update(),
        None if deleted_active else gr.update(),
        None if deleted_active else gr.update(),
        None if deleted_active else current_generation_id,
        None if deleted_active else current_saved_soundfont,
        None if deleted_active else current_audio_path,
        get_rerender_button_update(
            soundfont_choice, None if deleted_active else midi_path
        ),
    )


def refresh_history(selected_id):
    """Refresh entries and preserve selection only while its generation exists."""
    choices = get_history_choices()
    selection = (
        selected_id if any(value == selected_id for _, value in choices) else None
    )
    return gr.update(choices=choices, value=selection)


PIANO_ROLL_RESIZE_JS = """
() => {
    const resizePianoRoll = () => {
        const pianoRoll = document.querySelector("#piano-roll .js-plotly-plot");
        if (pianoRoll && window.Plotly?.Plots?.resize) {
            window.Plotly.Plots.resize(pianoRoll);
        }
        window.dispatchEvent(new Event("resize"));
    };

    requestAnimationFrame(() => requestAnimationFrame(resizePianoRoll));
    setTimeout(resizePianoRoll, 150);
}
"""


def create_demo(playback_status=None):
    """Build and return the Gradio demo."""
    default_soundfont = get_selected_soundfont()
    if playback_status is None:
        playback_status = is_playback_available(default_soundfont)

    playback_available, _playback_error = playback_status

    with gr.Blocks() as demo:
        # State for sidebar visibility
        sidebar_visible = gr.State(value=False)
        current_generation_id = gr.State(value=None)
        current_saved_soundfont = gr.State(value=None)
        current_audio_path = gr.State(value=None)

        # Header with title centered on the original full-width layout
        with gr.Row(elem_classes=["app-header"]):
            gr.Markdown("<h1 class='center-title'>Conductor</h1>")
            history_toggle_btn = gr.Button(
                "History",
                size="sm",
                elem_classes=["history-toggle"],
            )

        # Main content area with sidebar
        with gr.Row():
            # Main content column
            with gr.Column(scale=3):
                # Text to MIDI Tab for generating loops based on user input
                with gr.Tab(label="Text to MIDI"):
                    gr.Markdown("Generate a loop based on your description.")
                    with gr.Row(), gr.Accordion("API Keys", open=False):
                        openai_key_input = gr.Textbox(
                            lines=1, type="password", label="OpenAI API Key", value=""
                        )
                        gemini_key_input = gr.Textbox(
                            lines=1, type="password", label="Gemini API Key", value=""
                        )
                        claude_key_input = gr.Textbox(
                            lines=1, type="password", label="Claude API Key", value=""
                        )
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("## Loop Parameters")
                            key_input = gr.Dropdown(
                                choices=KEY_CHOICES,
                                label="Key",
                                value="C",
                            )
                            mode_input = gr.Dropdown(
                                choices=["Major", "minor"], label="Scale", value="Major"
                            )
                            description_input = gr.Textbox(
                                label="Description", value="A rhythmic sad pop song"
                            )
                        with gr.Column():
                            gr.Markdown("## Generation Parameters")
                            default_provider = DEFAULT_PROVIDER
                            # No model choice selects the provider's first model,
                            # which is its newest in Core's registry.
                            default_settings = get_model_settings(
                                default_provider, None, False
                            )
                            # The user's last free temperature, kept while a
                            # model shows a fixed one, and the active control.
                            requested_temperature = gr.State(DEFAULT_TEMPERATURE)
                            reasoning_control = gr.State(
                                default_settings["reasoning_control"]
                            )
                            provider_input = gr.Dropdown(
                                choices=get_providers(),
                                label="Provider",
                                value=default_provider,
                            )
                            model_choice_input = gr.Dropdown(
                                choices=get_model_dropdown_choices(default_provider),
                                label="Model",
                                value=default_settings["selected_model"],
                            )
                            temp_input = gr.Slider(
                                0.0,
                                1.0,
                                step=0.1,
                                value=default_settings["temperature_value"],
                                label="Temperature",
                                visible=get_temperature_visibility(
                                    default_settings["show_temperature"]
                                ),
                                interactive=default_settings["temperature_interactive"],
                            )
                            thinking_checkbox = gr.Checkbox(
                                label="Reasoning",
                                value=default_settings["thinking_value"],
                                visible=default_settings["show_thinking"],
                            )
                            effort_input = gr.Dropdown(
                                choices=default_settings["effort_options"],
                                label="Reasoning Effort",
                                value=default_settings["effort_value"],
                                visible=default_settings["show_effort"],
                            )
                            with gr.Accordion(
                                "Advanced Settings",
                                open=False,
                                visible=default_provider == "Ollama",
                            ) as advanced_settings:
                                num_ctx_input = gr.Dropdown(
                                    choices=OLLAMA_CONTEXT_SIZE_CHOICES,
                                    label="Ollama Context Size",
                                    value=0,
                                )
                    with gr.Row():
                        prog_button = gr.Button("Generate Loop", variant="primary")
                        stop_waiting_button = gr.Button(
                            "Stop Waiting", variant="stop", visible=False
                        )

                    # Output section
                    with gr.Row(), gr.Column():
                        prog_output = gr.File(label="Download Generated MIDI")
                        # Audio playback component
                        audio_output = gr.Audio(
                            label="Playback",
                            type="filepath",
                            interactive=False,
                            loop=True,
                        )
                        # Show playback status if not available
                        if not playback_available:
                            gr.Markdown(
                                f"*{get_soundfont_status_message(default_soundfont)}*",
                                elem_classes=["warning-text"],
                            )

                    with gr.Row(equal_height=False):
                        soundfont_input = gr.Dropdown(
                            choices=get_soundfont_choices(),
                            label="SoundFont",
                            value=default_soundfont,
                            interactive=True,
                        )
                        with gr.Column():
                            refresh_soundfonts_button = gr.Button("Refresh SoundFonts")
                            rerender_button = gr.Button(
                                "Re-render Audio",
                                interactive=rerender_available(default_soundfont, None),
                            )

                    vis_output = gr.Plot(
                        label="MIDI Visualization", elem_id="piano-roll"
                    )
                    error_message = gr.Textbox(label="Status", interactive=False)

                    # Every model control refreshes the dependent controls from
                    # the current choices, so they carry over where supported.
                    control_inputs = [
                        provider_input,
                        model_choice_input,
                        thinking_checkbox,
                        effort_input,
                        requested_temperature,
                        reasoning_control,
                    ]
                    control_outputs = [
                        model_choice_input,
                        temp_input,
                        thinking_checkbox,
                        effort_input,
                        reasoning_control,
                    ]
                    provider_input.input(
                        sync_controls_for_provider,
                        inputs=[
                            control
                            for control in control_inputs
                            if control is not model_choice_input
                        ],
                        outputs=control_outputs,
                    )
                    for control, sync_controls in (
                        (model_choice_input, sync_controls_for_model),
                        (effort_input, sync_controls_for_effort),
                        (thinking_checkbox, sync_controls_for_thinking),
                    ):
                        control.input(
                            sync_controls,
                            inputs=control_inputs,
                            outputs=control_outputs,
                        )
                    # A locked slider takes no input, so this holds the free value.
                    temp_input.input(
                        lambda value: value,
                        inputs=temp_input,
                        outputs=requested_temperature,
                    )
                    # .change also covers provider updates from loading history.
                    provider_input.change(
                        sync_context_size_for_provider,
                        inputs=provider_input,
                        outputs=advanced_settings,
                    )
                    # When the user clicks the button, run the loop generation function based on the current inputs.
                    # Capture the event so the stop-waiting button can detach the UI from the in-flight request.
                    gen_event = prog_button.click(
                        run_loop,
                        inputs=[
                            key_input,
                            mode_input,
                            description_input,
                            temp_input,
                            model_choice_input,
                            thinking_checkbox,
                            effort_input,
                            soundfont_input,
                            openai_key_input,
                            gemini_key_input,
                            claude_key_input,
                            num_ctx_input,
                            provider_input,
                        ],
                        outputs=[
                            prog_output,
                            audio_output,
                            vis_output,
                            error_message,
                            stop_waiting_button,
                            current_generation_id,
                            current_saved_soundfont,
                            current_audio_path,
                        ],
                    )
                    # Stop Waiting detaches the UI from the API response wait and hides itself.
                    stop_waiting_button.click(
                        fn=lambda: (
                            None,
                            None,
                            None,
                            "Stopped waiting. The provider request may still finish in the background.",
                            gr.update(visible=False),
                            None,
                            None,
                            None,
                        ),
                        outputs=[
                            prog_output,
                            audio_output,
                            vis_output,
                            error_message,
                            stop_waiting_button,
                            current_generation_id,
                            current_saved_soundfont,
                            current_audio_path,
                        ],
                        cancels=[gen_event],
                    ).then(
                        get_rerender_button_update,
                        inputs=[soundfont_input, prog_output],
                        outputs=[rerender_button],
                    )
                    rerender_button.click(
                        rerender_current_audio,
                        inputs=[
                            prog_output,
                            soundfont_input,
                            current_saved_soundfont,
                            current_generation_id,
                            current_audio_path,
                        ],
                        outputs=[
                            audio_output,
                            error_message,
                            current_saved_soundfont,
                            current_audio_path,
                        ],
                    )
                    refresh_soundfonts_button.click(
                        refresh_soundfont_controls,
                        inputs=[soundfont_input, prog_output],
                        outputs=[soundfont_input, rerender_button, error_message],
                    )

                # Prompt Editor Tab to allow users to edit the system prompts used in the generation process
                with gr.Tab(label="Prompt Editor"):
                    gr.Markdown("## Edit System Prompt")
                    loop_gen_text = get_prompt_editor_text()
                    # Create text boxes for the user to edit the prompts
                    gr.Markdown("### Loop Generation Prompt")
                    gr.Markdown(
                        "This prompt is used to generate the loop based on the description."
                    )
                    loop_gen_input = gr.Textbox(lines=30, value=loop_gen_text)
                    save_button = gr.Button("Save Prompt")
                    save_status = gr.Textbox(label="Status", interactive=False)
                    # When the user clicks the save button, save the current prompts in the textboxes to the text files
                    save_button.click(
                        save_prompts,
                        inputs=[loop_gen_input],
                        outputs=[save_status],
                    )

            # History sidebar (initially hidden)
            with gr.Column(
                scale=1, visible=False, elem_classes=["history-sidebar"]
            ) as history_sidebar:
                gr.Markdown("## History")

                with gr.Row() as history_actions:
                    load_btn = gr.Button("Load", size="sm", variant="primary")
                    delete_btn = gr.Button("Delete...", size="sm", variant="stop")
                    refresh_btn = gr.Button("Refresh", size="sm")
                with gr.Row(visible=False) as delete_confirmation:
                    confirm_delete_btn = gr.Button(
                        "Confirm Delete", size="sm", variant="stop"
                    )
                    cancel_delete_btn = gr.Button("Cancel", size="sm")
                history_status = gr.Textbox(label="History status", interactive=False)
                history_list = gr.Radio(
                    label="Recent Generations",
                    choices=get_history_choices(),
                    interactive=True,
                    elem_classes=["history-list"],
                )

        # History sidebar toggle
        history_toggle_event = history_toggle_btn.click(
            toggle_history_sidebar,
            inputs=[sidebar_visible, history_list],
            outputs=[
                sidebar_visible,
                history_toggle_btn,
                history_sidebar,
                history_list,
                history_actions,
                delete_confirmation,
            ],
        )
        history_toggle_event.then(fn=None, js=PIANO_ROLL_RESIZE_JS, queue=False)

        # .input fires only on user selection, not on programmatic list updates.
        history_list.input(
            select_history_item,
            inputs=[history_list],
            outputs=[history_status],
        ).then(
            hide_delete_confirmation,
            outputs=[history_actions, delete_confirmation],
        )

        # Load history item into main view
        load_btn.click(
            load_history_item,
            inputs=[history_list],
            outputs=[
                prog_output,
                audio_output,
                soundfont_input,
                vis_output,
                error_message,
                current_generation_id,
                current_saved_soundfont,
                current_audio_path,
                rerender_button,
                key_input,
                mode_input,
                description_input,
                provider_input,
                model_choice_input,
                temp_input,
                thinking_checkbox,
                effort_input,
                requested_temperature,
                reasoning_control,
            ],
        ).then(
            loaded_history_status,
            inputs=[current_generation_id],
            outputs=[history_status],
        )

        delete_btn.click(
            show_delete_confirmation,
            inputs=[history_list],
            outputs=[history_actions, delete_confirmation, history_status],
        )
        cancel_delete_btn.click(
            cancel_delete_confirmation,
            outputs=[history_actions, delete_confirmation, history_status],
        )
        confirm_delete_btn.click(
            delete_history_item,
            inputs=[
                history_list,
                current_generation_id,
                soundfont_input,
                prog_output,
                current_saved_soundfont,
                current_audio_path,
            ],
            outputs=[
                history_list,
                history_status,
                history_actions,
                delete_confirmation,
                prog_output,
                audio_output,
                vis_output,
                current_generation_id,
                current_saved_soundfont,
                current_audio_path,
                rerender_button,
            ],
        )

        refresh_btn.click(
            refresh_history,
            inputs=[history_list],
            outputs=[history_list],
        )

        gen_event.then(
            get_rerender_button_update,
            inputs=[soundfont_input, prog_output],
            outputs=[rerender_button],
        ).then(
            refresh_history,
            inputs=[history_list],
            outputs=[history_list],
        )

    return demo


def main():
    """Run the Gradio app."""
    # Surface conductor_core (and app) log records on the console. Core only
    # emits records; configuring handlers is the application's responsibility.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    default_soundfont = get_selected_soundfont()
    playback_status = is_playback_available(default_soundfont)
    playback_available, _ = playback_status
    if not playback_available:
        print(f"Warning: {get_playback_status_message(default_soundfont)}")

    demo = create_demo(playback_status=playback_status)
    demo.launch(
        allowed_paths=[str(Path(HISTORY_STORE.artifact_root).resolve())],
        css=APP_CSS,
    )


if __name__ == "__main__":
    main()
