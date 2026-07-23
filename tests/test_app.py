from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

from conductor_core.storage import GenerationMetadata

from conductor_main import app


def _write_binary_file(path: Path, content: bytes = b"data") -> Path:
    path.write_bytes(content)
    return path


def test_run_loop_passes_ui_configuration_to_core(monkeypatch, tmp_path):
    captured = {}
    midi_path = tmp_path / "loop.mid"
    midi_path.write_bytes(b"midi")
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")
    monkeypatch.setattr(app, "load_app_prompt_override", lambda: "override prompt")
    monkeypatch.setattr(app, "MidiFile", lambda path: "midi")
    monkeypatch.setattr(app, "visualize_midi_plotly", lambda midi: "viz")

    class FakeEngine:
        def __init__(self, config):
            captured["config"] = config

        def generate(self, request, progress_callback=None):
            captured["request"] = request
            if progress_callback:
                progress_callback(
                    SimpleNamespace(stage="provider_call", message="Generating MIDI...")
                )
            return SimpleNamespace(
                midi_path=str(midi_path),
                audio_path=None,
                cost=0.25,
                generation_id="fixed_id",
                metadata=SimpleNamespace(soundfont=None),
            )

    monkeypatch.setattr(app, "LoopGenerationEngine", FakeEngine)

    outputs = list(
        app.run_loop(
            key="C",
            scale="Major",
            description="warm rhodes loop",
            temp=0.3,
            model_choice="gpt-test",
            use_thinking=False,
            effort="low",
            soundfont_choice="custom.sf2",
            openai_key=" openai-key ",
            gemini_key=" gemini-key ",
            claude_key=" claude-key ",
        )
    )

    final_output = outputs[-1]

    assert final_output[0] == str(midi_path)
    assert final_output[2] == "viz"
    assert captured["config"].prompt_override == "override prompt"
    assert captured["config"].provider_credentials.openai_api_key == "openai-key"
    assert captured["config"].provider_credentials.google_api_key == "gemini-key"
    assert captured["config"].provider_credentials.anthropic_api_key == "claude-key"
    assert captured["config"].default_soundfont_path == "custom.sf2"
    assert captured["config"].max_generations == app.MAX_HISTORY_GENERATIONS
    assert captured["request"].provider is None
    assert captured["request"].effort == "low"
    assert captured["request"].render_audio is True
    assert captured["request"].soundfont_path == "custom.sf2"
    assert captured["request"].description == "warm rhodes loop"


def test_run_loop_reports_core_generation_errors(monkeypatch):
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")

    class FailingEngine:
        def __init__(self, config):
            pass

        def generate(self, request, progress_callback=None):
            raise ValueError("provider failed")

    monkeypatch.setattr(app, "LoopGenerationEngine", FailingEngine)

    outputs = list(
        app.run_loop(
            key="C",
            scale="Major",
            description="warm rhodes loop",
            temp=0.3,
            model_choice="gpt-test",
            use_thinking=False,
            effort="low",
            soundfont_choice="custom.sf2",
            openai_key="",
            gemini_key="",
            claude_key="",
        )
    )

    assert outputs[-1][3] == "provider failed"
    assert outputs[-1][4] == {"visible": False}


def test_run_loop_close_does_not_wait_for_in_flight_provider_call(monkeypatch):
    provider_started = Event()
    release_provider = Event()
    provider_finished = Event()
    close_finished = Event()

    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")

    class SlowEngine:
        def __init__(self, config):
            pass

        def generate(self, request, progress_callback=None):
            provider_started.set()
            release_provider.wait()
            provider_finished.set()

    monkeypatch.setattr(app, "LoopGenerationEngine", SlowEngine)
    generator = app.run_loop(
        key="C",
        scale="Major",
        description="warm rhodes loop",
        temp=0.3,
        model_choice="gpt-test",
        use_thinking=False,
        effort="low",
        soundfont_choice="custom.sf2",
        openai_key="",
        gemini_key="",
        claude_key="",
    )

    closer = None
    try:
        next(generator)
        next(generator)
        assert provider_started.wait(timeout=1)

        closer = Thread(target=lambda: (generator.close(), close_finished.set()))
        closer.start()

        assert close_finished.wait(timeout=1)
        assert not provider_finished.is_set()
    finally:
        release_provider.set()
        assert provider_finished.wait(timeout=1)
        if closer is not None:
            closer.join(timeout=1)


def test_get_selected_soundfont_prefers_requested_choice(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_soundfont_choices",
        lambda: ["FM-Piano1 20190916.sf2", "custom.sf2"],
    )
    monkeypatch.setattr(
        app,
        "get_default_soundfont",
        lambda: str(Path("soundfonts") / "FM-Piano1 20190916.sf2"),
    )

    selected_soundfont = app.get_selected_soundfont("custom.sf2")

    assert selected_soundfont == "custom.sf2"


def test_default_model_exists_in_model_metadata():
    model_info = app.get_model_info()

    assert app.DEFAULT_PROVIDER in model_info["models"]
    assert app.DEFAULT_MODEL in model_info["models"][app.DEFAULT_PROVIDER]


def test_core_legacy_generation_metadata_defaults_reasoning_to_none():
    metadata = GenerationMetadata.model_validate(
        {
            "id": "legacy",
            "timestamp": "2026-07-21T12:00:00Z",
            "prompt": "legacy prompt",
            "key": "C",
            "scale": "Major",
            "model": "legacy-model",
            "provider": "OpenAI",
            "temperature": 0.1,
            "midi_path": "loop.mid",
        }
    )

    assert metadata.use_thinking is None
    assert metadata.effort is None


def test_model_settings_use_core_supported_effort_values():
    model_info = app.get_model_info()

    for provider, models in model_info["models"].items():
        for model, metadata in models.items():
            effort_options = metadata.get("effort_options", [])
            if effort_options:
                settings = app.get_model_settings(provider, model)

                assert settings["effort_options"] == effort_options
                assert settings["effort_value"] in effort_options


def test_history_controls_restore_known_effort_model_exactly(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "reasoning-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "medium", "high"],
                    }
                }
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="F#/Gb",
            scale="minor",
            prompt="restored prompt",
            provider="OpenAI",
            model="reasoning-model",
            temperature=0.7,
            use_thinking=False,
            effort="high",
        )
    )

    assert updates.key == {"value": "F#/Gb"}
    assert updates.scale == {"value": "minor"}
    assert updates.description == {"value": "restored prompt"}
    assert updates.provider["value"] == "OpenAI"
    assert updates.model["value"] == "reasoning-model"
    assert updates.temperature == {"visible": False, "value": 0.7}
    assert updates.use_thinking == {"visible": False, "value": False}
    assert updates.effort == {
        "choices": ["low", "medium", "high"],
        "value": "high",
        "visible": True,
    }
    assert updates.warnings == ()


def test_history_controls_restore_known_toggle_model_exactly(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {"toggle-model": {"extended_thinking": True, "effort_options": []}}
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="think deeply",
            provider="Anthropic",
            model="toggle-model",
            temperature=0.4,
            use_thinking=True,
            effort="low",
        )
    )

    assert updates.temperature == {"visible": False, "value": 0.4}
    assert updates.use_thinking == {"visible": True, "value": True}
    assert updates.effort == {"choices": ["low"], "value": "low", "visible": False}
    assert updates.warnings == ()


def test_history_controls_use_defaults_and_warn_for_legacy_reasoning(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {"toggle-model": {"extended_thinking": True, "effort_options": []}}
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="legacy",
            provider="Anthropic",
            model="toggle-model",
            temperature=0.2,
            use_thinking=None,
            effort=None,
        )
    )

    assert updates.use_thinking == {"visible": True, "value": False}
    assert updates.effort == {"choices": ["low"], "value": "low", "visible": False}
    assert updates.warnings == ("Reasoning settings weren't saved; defaults applied.",)


def test_history_controls_preserve_unavailable_provider_and_model_without_discovery(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {"models": {"OpenAI": {"current-model": {}}}},
    )
    monkeypatch.setattr(
        app.ollama_api,
        "get_ollama_status",
        lambda: (_ for _ in ()).throw(AssertionError("must not discover Ollama")),
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="local history",
            provider="Ollama",
            model="retired-local-model",
            temperature=0.5,
            use_thinking=True,
            effort="medium",
        )
    )

    assert updates.provider["choices"][-1] == ("Ollama (unavailable)", "Ollama")
    assert updates.provider["value"] == "Ollama"
    assert updates.model["choices"] == [
        ("retired-local-model (unavailable)", "retired-local-model")
    ]
    assert updates.model["value"] == "retired-local-model"
    assert updates.use_thinking["value"] is True
    assert updates.effort["value"] == "medium"
    assert updates.warnings == ("Unavailable selection: Ollama / retired-local-model.",)


def test_history_store_uses_the_app_retention_policy():
    assert app.HISTORY_STORE.max_generations == app.MAX_HISTORY_GENERATIONS


def test_prompt_override_uses_the_app_data_directory(monkeypatch, tmp_path):
    override_path = tmp_path / "Prompts" / "loop gen.txt"
    monkeypatch.setattr(app, "PROMPT_OVERRIDE_PATH", override_path)

    assert app.load_app_prompt_override() is None
    assert app.save_prompts("standalone override").startswith("Prompts saved successfully")
    assert override_path.read_text(encoding="utf-8") == "standalone override"
    assert app.load_app_prompt_override() == "standalone override"


def test_rerender_current_audio_skips_existing_matching_soundfont(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")

    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")

    def fail_render(*args, **kwargs):
        raise AssertionError("midi_to_mp3 should not be called when the audio is already current")

    monkeypatch.setattr(app, "midi_to_mp3", fail_render)

    rerendered_audio_path, status, saved_soundfont, current_audio_path = app.rerender_current_audio(
        str(midi_path),
        "custom.sf2",
        "custom.sf2",
        "gen_1",
        str(audio_path),
    )

    assert rerendered_audio_path == str(audio_path)
    assert status == "Audio already rendered with custom.sf2."
    assert saved_soundfont == "custom.sf2"
    assert current_audio_path == str(audio_path)


def test_rerender_current_audio_updates_saved_generation(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    rendered_audio = _write_binary_file(tmp_path / "rendered.mp3", b"rendered")

    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")
    monkeypatch.setattr(
        app,
        "midi_to_mp3",
        lambda midi_path, output_path=None, soundfont_name=None: str(rendered_audio),
    )
    monkeypatch.setattr(
        app,
        "update_generation_audio",
        lambda gen_id, audio_path, soundfont=None: SimpleNamespace(
            audio_path=str(tmp_path / "saved-loop.mp3"),
            soundfont=soundfont,
        ),
    )

    rerendered_audio_path, status, saved_soundfont, current_audio_path = app.rerender_current_audio(
        str(midi_path),
        "custom.sf2",
        "old.sf2",
        "gen_1",
        None,
    )

    assert rerendered_audio_path == str(tmp_path / "saved-loop.mp3")
    assert status == "Rendered audio with custom.sf2."
    assert saved_soundfont == "custom.sf2"
    assert current_audio_path == str(tmp_path / "saved-loop.mp3")


def test_load_history_item_warns_when_saved_soundfont_is_missing(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")

    monkeypatch.setattr(app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"])
    monkeypatch.setattr(
        app,
        "get_default_soundfont",
        lambda: str(Path("soundfonts") / "FM-Piano1 20190916.sf2"),
    )
    monkeypatch.setattr(
        app,
        "get_generation",
        lambda gen_id: SimpleNamespace(
            midi_path=str(midi_path),
            audio_path=str(audio_path),
            soundfont="missing.sf2",
            id=gen_id,
            key="D",
            scale="minor",
            prompt="saved prompt",
            provider="Google",
            model=app.DEFAULT_MODEL,
            temperature=0.6,
            use_thinking=False,
            effort="low",
        ),
    )
    monkeypatch.setattr(app, "MidiFile", lambda path: object())
    monkeypatch.setattr(app, "visualize_midi_plotly", lambda midi: "viz")
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (True, None))
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    (
        loaded_midi_path,
        loaded_audio_path,
        dropdown_update,
        visualization,
        error_message,
        generation_id,
        saved_soundfont,
        current_audio_path,
        rerender_update,
        key_update,
        scale_update,
        description_update,
        provider_update,
        model_update,
        temperature_update,
        thinking_update,
        effort_update,
    ) = app.load_history_item("gen_1")

    assert loaded_midi_path == str(midi_path)
    assert loaded_audio_path == str(audio_path)
    assert dropdown_update["value"] == "FM-Piano1 20190916.sf2"
    assert visualization == "viz"
    assert error_message == "Missing SoundFont: missing.sf2."
    assert generation_id == "gen_1"
    assert saved_soundfont == "missing.sf2"
    assert current_audio_path == str(audio_path)
    assert rerender_update["interactive"] is True
    assert key_update["value"] == "D"
    assert scale_update["value"] == "minor"
    assert description_update["value"] == "saved prompt"
    assert provider_update["value"] == "Google"
    assert model_update["value"] == app.DEFAULT_MODEL
    assert temperature_update["value"] == 0.6
    assert thinking_update["value"] is False
    assert effort_update["value"] == "low"


def test_load_history_item_error_paths_preserve_parameter_controls(monkeypatch, tmp_path):
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_soundfont_choices", lambda: [])
    monkeypatch.setattr(app, "get_default_soundfont", lambda: None)
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (False, None))

    monkeypatch.setattr(app, "get_generation", lambda gen_id: None)
    no_selection = app.load_history_item(None)
    not_found = app.load_history_item("missing")

    monkeypatch.setattr(
        app,
        "get_generation",
        lambda gen_id: SimpleNamespace(
            midi_path=str(tmp_path / "missing.mid"),
            soundfont=None,
        ),
    )
    missing_midi = app.load_history_item("missing-midi")

    for result in (no_selection, not_found, missing_midi):
        assert len(result) == 17
        assert result[-8:] == ({}, {}, {}, {}, {}, {}, {}, {})


def test_refresh_soundfont_controls_updates_dropdown_choices(monkeypatch):
    midi_path = _write_binary_file(Path("active.mid"))

    monkeypatch.setattr(app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"])
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (True, None))
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    try:
        dropdown_update, rerender_update, status_message = app.refresh_soundfont_controls(
            "new.sf2",
            str(midi_path),
        )

        assert dropdown_update["choices"] == ["FM-Piano1 20190916.sf2", "new.sf2"]
        assert dropdown_update["value"] == "new.sf2"
        assert rerender_update["interactive"] is True
        assert status_message == "Found 2 SoundFonts. Selected new.sf2."
    finally:
        midi_path.unlink(missing_ok=True)


def test_refresh_soundfont_controls_prefers_dependency_status_message(monkeypatch):
    monkeypatch.setattr(app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"])
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(
        app,
        "is_playback_available",
        lambda soundfont_name=None: (False, "FluidSynth is not installed or not in PATH"),
    )
    monkeypatch.setattr(
        app,
        "get_playback_status_message",
        lambda soundfont_name=None: (
            "Audio playback is not available. Setup required:\n  - Install FluidSynth: https://github.com/FluidSynth/fluidsynth/releases"
        ),
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    dropdown_update, rerender_update, status_message = app.refresh_soundfont_controls(
        "new.sf2", None
    )

    assert dropdown_update["choices"] == ["FM-Piano1 20190916.sf2", "new.sf2"]
    assert dropdown_update["value"] == "new.sf2"
    assert rerender_update["interactive"] is False
    assert status_message == (
        "Audio playback is not available. Setup required:\n"
        "  - Install FluidSynth: https://github.com/FluidSynth/fluidsynth/releases"
    )


def test_get_rerender_button_update_requires_active_midi(monkeypatch):
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (True, None))
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    rerender_update = app.get_rerender_button_update("new.sf2", None)

    assert rerender_update["interactive"] is False


def test_delete_history_item_disables_rerender_for_deleted_loaded_generation(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")

    monkeypatch.setattr(app, "delete_generation", lambda gen_id: True)
    monkeypatch.setattr(app, "get_history_choices", lambda: ["gen_2"])
    monkeypatch.setattr(app, "render_history_html", lambda: "<div>history</div>")
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (True, None))
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    (
        dropdown_update,
        status_message,
        history_html,
        cleared_midi_path,
        cleared_audio_path,
        cleared_visualization,
        current_generation_id,
        current_saved_soundfont,
        current_audio_path,
        rerender_update,
    ) = app.delete_history_item(
        "gen_1",
        current_generation_id="gen_1",
        soundfont_choice="new.sf2",
        midi_path=str(midi_path),
        current_saved_soundfont="old.sf2",
        current_audio_path=str(audio_path),
    )

    assert dropdown_update == {"choices": ["gen_2"], "value": None}
    assert status_message == "Deleted generation"
    assert history_html == "<div>history</div>"
    assert cleared_midi_path is None
    assert cleared_audio_path is None
    assert cleared_visualization is None
    assert current_generation_id is None
    assert current_saved_soundfont is None
    assert current_audio_path is None
    assert rerender_update["interactive"] is False


def test_render_history_html_displays_zero_cost(monkeypatch):
    monkeypatch.setattr(
        app,
        "load_history",
        lambda: [
            SimpleNamespace(
                id="20260101_120000",
                timestamp=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                prompt="local model loop",
                key="C",
                scale="Major",
                model="llama3",
                cost=0,
            )
        ],
    )

    html = app.render_history_html()

    assert "Cost: $0.0000" in html
    assert "Cost: N/A" not in html


def test_render_history_html_displays_missing_cost_as_na(monkeypatch):
    monkeypatch.setattr(
        app,
        "load_history",
        lambda: [
            SimpleNamespace(
                id="20260101_120000",
                timestamp=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                prompt="cloud model loop",
                key="C",
                scale="Major",
                model="gpt-5-mini",
                cost=None,
            )
        ],
    )

    html = app.render_history_html()

    assert "Cost: N/A" in html


def test_render_history_html_pairs_model_with_reasoning_details(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "effort-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "medium", "high", "xhigh"],
                    }
                },
                "Anthropic": {
                    "toggle-model": {
                        "extended_thinking": True,
                        "effort_options": [],
                    }
                },
            }
        },
    )
    history_defaults = {
        "timestamp": __import__("datetime").datetime(2026, 1, 1, 12, 0),
        "prompt": "history reasoning",
        "key": "C",
        "scale": "Major",
        "cost": None,
    }
    monkeypatch.setattr(
        app,
        "load_history",
        lambda: [
            SimpleNamespace(
                **history_defaults,
                id="effort",
                provider="OpenAI",
                model="effort-model",
                use_thinking=False,
                effort="xhigh",
            ),
            SimpleNamespace(
                **history_defaults,
                id="toggle",
                provider="Anthropic",
                model="toggle-model",
                use_thinking=True,
                effort="low",
            ),
            SimpleNamespace(
                **history_defaults,
                id="legacy",
                provider="OpenAI",
                model="legacy-model",
                use_thinking=None,
                effort=None,
            ),
            SimpleNamespace(
                **history_defaults,
                id="toggle-off",
                provider="Anthropic",
                model="toggle-off-model",
                use_thinking=False,
                effort="low",
            ),
        ],
    )

    rendered_history = app.render_history_html()

    assert "effort-model (xhigh)" in rendered_history
    assert "toggle-model (reasoning)" in rendered_history
    assert "legacy-model (" not in rendered_history
    assert "toggle-off-model (" not in rendered_history


def test_render_history_html_escapes_persisted_metadata(monkeypatch):
    monkeypatch.setattr(
        app,
        "load_history",
        lambda: [
            SimpleNamespace(
                id='"><script>alert(1)</script>',
                timestamp=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                prompt="<img src=x onerror=alert(1)>",
                key="<b>C</b>",
                scale="<i>Major</i>",
                model="<em>model</em>",
                provider="OpenAI",
                use_thinking=False,
                effort="<script>effort</script>",
                cost=0,
            )
        ],
    )
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "<em>model</em>": {
                        "extended_thinking": True,
                        "effort_options": ["<script>effort</script>"],
                    }
                }
            }
        },
    )

    rendered_history = app.render_history_html()

    assert 'data-id="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in rendered_history
    assert "&lt;b&gt;C&lt;/b&gt; &lt;i&gt;Major&lt;/i&gt;" in rendered_history
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered_history
    assert "&lt;em&gt;model&lt;/em&gt;" in rendered_history
    assert "&lt;script&gt;effort&lt;/script&gt;" in rendered_history
    assert "<script>alert(1)</script>" not in rendered_history
    assert "<img src=x onerror=alert(1)>" not in rendered_history
    assert "<script>effort</script>" not in rendered_history


def test_refresh_soundfont_controls_stays_disabled_after_active_delete(monkeypatch):
    monkeypatch.setattr(app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"])
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont_name=None: (True, None))
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    _, rerender_update, _ = app.refresh_soundfont_controls("new.sf2", None)

    assert rerender_update["interactive"] is False


def test_history_toggle_resizes_piano_roll_after_sidebar_update():
    demo = app.create_demo(playback_status=(True, None))
    dependencies = demo.config["dependencies"]
    toggle_dependency = next(
        dependency
        for dependency in dependencies
        if dependency["api_name"] == "toggle_history_sidebar"
    )
    resize_dependency = next(
        dependency for dependency in dependencies if dependency["js"] == app.PIANO_ROLL_RESIZE_JS
    )

    piano_roll = next(
        component
        for component in demo.config["components"]
        if component["props"].get("elem_id") == "piano-roll"
    )

    assert piano_roll["type"] == "plot"
    assert resize_dependency["trigger_after"] == toggle_dependency["id"]
    assert resize_dependency["queue"] is False


def test_history_load_callback_updates_all_parameter_controls_once():
    demo = app.create_demo(playback_status=(True, None))
    dependency = next(
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] == "load_history_item"
    )
    components_by_id = {component["id"]: component for component in demo.config["components"]}
    restored_labels = [
        components_by_id[component_id]["props"].get("label")
        for component_id in dependency["outputs"][-8:]
    ]

    assert restored_labels == [
        "Key",
        "Scale",
        "Description",
        "Provider",
        "Model",
        "Temperature",
        "Reasoning",
        "Reasoning Effort",
    ]
    assert len(dependency["outputs"]) == len(set(dependency["outputs"]))


def test_model_sync_callbacks_only_run_for_user_input():
    demo = app.create_demo(playback_status=(True, None))
    sync_api_names = {
        "sync_controls_for_provider",
        "sync_controls_for_model",
        "sync_controls_for_thinking",
    }
    sync_dependencies = [
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] in sync_api_names
    ]

    assert {dependency["api_name"] for dependency in sync_dependencies} == sync_api_names
    assert all(dependency["targets"][0][1] == "input" for dependency in sync_dependencies)


def test_main_allows_gradio_to_serve_generation_history(monkeypatch, tmp_path):
    artifact_root = tmp_path / "app-data" / "generations"
    launched_with = {}

    class FakeDemo:
        def launch(self, **kwargs):
            launched_with.update(kwargs)

    monkeypatch.setattr(app, "HISTORY_STORE", SimpleNamespace(artifact_root=str(artifact_root)))
    monkeypatch.setattr(app, "get_selected_soundfont", lambda: None)
    monkeypatch.setattr(app, "is_playback_available", lambda soundfont=None: (True, None))
    monkeypatch.setattr(app, "create_demo", lambda playback_status=None: FakeDemo())

    app.main()

    assert launched_with == {
        "allowed_paths": [str(artifact_root.resolve())],
        "css": app.APP_CSS,
    }


def _clear_data_directory_environment(monkeypatch):
    monkeypatch.delenv("CONDUCTOR_MAIN_DATA_DIR", raising=False)
    monkeypatch.delenv("CONDUCTOR_MAIN_SOUNDFONT_DIR", raising=False)
    monkeypatch.delenv("CONDUCTOR_HOME", raising=False)


def test_app_data_dir_defaults_to_the_conductor_main_directory(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert app._resolve_conductor_home() == tmp_path / ".conductor"
    assert app._resolve_app_data_dir() == tmp_path / ".conductor" / "main"
    assert app._resolve_app_soundfont_dir() == tmp_path / ".conductor" / "main" / "soundfonts"


def test_app_data_dir_honors_conductor_home(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    conductor_home = tmp_path / "suite-data"
    monkeypatch.setenv("CONDUCTOR_HOME", str(conductor_home))

    assert app._resolve_conductor_home() == conductor_home
    assert app._resolve_app_data_dir() == conductor_home / "main"


def test_app_data_dir_override_wins_over_conductor_home(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    app_data_dir = tmp_path / "main-data"
    monkeypatch.setenv("CONDUCTOR_HOME", str(tmp_path / "suite-data"))
    monkeypatch.setenv("CONDUCTOR_MAIN_DATA_DIR", str(app_data_dir))

    assert app._resolve_app_data_dir() == app_data_dir
    assert app._resolve_app_soundfont_dir() == app_data_dir / "soundfonts"


def test_soundfont_dir_override_wins_over_app_data_dir(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    soundfont_dir = tmp_path / "shared-soundfonts"
    monkeypatch.setenv("CONDUCTOR_MAIN_DATA_DIR", str(tmp_path / "main-data"))
    monkeypatch.setenv("CONDUCTOR_MAIN_SOUNDFONT_DIR", str(soundfont_dir))

    assert app._resolve_app_soundfont_dir() == soundfont_dir


def test_data_directory_overrides_expand_user_home(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CONDUCTOR_HOME", "~/suite-data")

    assert app._resolve_conductor_home() == tmp_path / "suite-data"

    monkeypatch.setenv("CONDUCTOR_MAIN_DATA_DIR", "~/main-data")
    monkeypatch.setenv("CONDUCTOR_MAIN_SOUNDFONT_DIR", "~/shared-soundfonts")

    assert app._resolve_app_data_dir() == tmp_path / "main-data"
    assert app._resolve_app_soundfont_dir() == tmp_path / "shared-soundfonts"


def test_app_data_dir_is_independent_of_installed_module_path(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        app,
        "__file__",
        str(tmp_path / "venv" / "Lib" / "site-packages" / "conductor_main" / "app.py"),
    )

    assert app._resolve_app_data_dir() == tmp_path / "home" / ".conductor" / "main"


def test_app_data_dir_honors_environment_override(monkeypatch, tmp_path):
    _clear_data_directory_environment(monkeypatch)
    monkeypatch.setenv("CONDUCTOR_MAIN_DATA_DIR", str(tmp_path))

    assert app._resolve_app_data_dir() == tmp_path
