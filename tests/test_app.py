from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

from conductor_core import AudioRenderingError, GenerationMetadata

from conductor_main import app


def _write_binary_file(path: Path, content: bytes = b"data") -> Path:
    path.write_bytes(content)
    return path


def test_normalize_key_for_core_defaults_combined_black_keys_to_sharps():
    expected_keys = {
        "C#/Db": "C#",
        "D#/Eb": "D#",
        "F#/Gb": "F#",
        "G#/Ab": "G#",
        "A#/Bb": "A#",
        "C": "C",
    }

    assert {
        key: app.normalize_key_for_core(key) for key in expected_keys
    } == expected_keys


def test_normalize_key_for_ui_coerces_core_enharmonics_to_twelve_choices():
    expected_keys = {
        "C#": "C#/Db",
        "Db": "C#/Db",
        "D#": "D#/Eb",
        "F#": "F#/Gb",
        "G#": "G#/Ab",
        "A#": "A#/Bb",
        "C##": "D",
        "Gbb": "F",
        "B#": "C",
        "C": "C",
    }

    assert {
        key: app.normalize_key_for_ui(key) for key in expected_keys
    } == expected_keys
    assert app.normalize_key_for_ui("not-a-key") is None


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
                warnings=["Audio rendering was skipped or failed."],
            )

    monkeypatch.setattr(app, "LoopGenerationEngine", FakeEngine)

    outputs = list(
        app.run_loop(
            key="F#/Gb",
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
    assert final_output[3] == "Audio rendering was skipped or failed."
    assert captured["config"].prompt_override == "override prompt"
    assert captured["config"].provider_credentials.openai_api_key == "openai-key"
    assert captured["config"].provider_credentials.google_api_key == "gemini-key"
    assert captured["config"].provider_credentials.anthropic_api_key == "claude-key"
    assert captured["config"].default_soundfont_path == "custom.sf2"
    assert captured["config"].max_generations == app.MAX_HISTORY_GENERATIONS
    assert captured["request"].effort == "low"
    assert captured["request"].ollama_num_ctx is None
    assert captured["request"].render_audio is True
    assert captured["request"].soundfont_path == "custom.sf2"
    assert captured["request"].description == "warm rhodes loop"
    assert captured["request"].key == "F#"


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


def test_default_model_is_the_newest_default_provider_model():
    model_info = app.get_model_info()
    newest_model = next(iter(model_info["models"][app.DEFAULT_PROVIDER]))

    settings = app.get_model_settings(app.DEFAULT_PROVIDER, None)

    assert settings["selected_model"] == newest_model


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
                adds_none = (
                    metadata.get("thinking_off") == "disabled"
                    and "none" not in effort_options
                )

                assert settings["effort_options"] == (
                    ["none", *effort_options] if adds_none else effort_options
                )
                assert settings["effort_value"] == settings["effort_options"][0]


def test_model_settings_follow_core_reasoning_and_temperature_metadata():
    model_info = app.get_model_info()

    for provider, models in model_info["models"].items():
        for model, metadata in models.items():
            settings = app.get_model_settings(provider, model)
            thinking = metadata.get("extended_thinking", False)
            effort_options = metadata.get("effort_options") or []
            toggles = (
                thinking
                and not effort_options
                and metadata.get("thinking_off") == "disabled"
            )

            assert settings["show_effort"] == bool(thinking and effort_options)
            assert settings["show_thinking"] == toggles
            added_none = settings["effort_value"] == "none" and "none" not in (
                effort_options
            )
            # Only the toggle and the "none" Main adds send use_thinking=False.
            assert settings["thinking_value"] == (
                thinking and not toggles and not added_none
            )
            assert settings["show_temperature"] == metadata.get(
                "temperature_supported", True
            )


def test_model_settings_hide_reasoning_for_always_on_models_without_levels(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Google": {
                    "always-on": {
                        "extended_thinking": True,
                        "thinking_off": "lowest_effort",
                    }
                }
            }
        },
    )

    settings = app.get_model_settings("Google", "always-on")

    assert settings["show_thinking"] is False
    assert settings["show_effort"] is False
    assert settings["thinking_value"] is True


def test_model_settings_show_fixed_temperature_only_while_thinking(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {
                    "toggle-model": {
                        "extended_thinking": True,
                        "thinking_off": "disabled",
                        "thinking_fixed_temperature": 1.0,
                    }
                }
            }
        },
    )

    thinking = app.get_model_settings("Anthropic", "toggle-model", True)
    not_thinking = app.get_model_settings("Anthropic", "toggle-model", False)

    assert thinking["show_temperature"] is True
    assert thinking["temperature_value"] == 1.0
    assert thinking["temperature_interactive"] is False
    assert not_thinking["temperature_value"] == 0.1
    assert not_thinking["temperature_interactive"] is True


def test_effort_models_that_can_disable_thinking_offer_none(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {
                    "adaptive-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "disabled",
                        "thinking_fixed_temperature": 1.0,
                    },
                    "always-on-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "lowest_effort",
                    },
                }
            }
        },
    )

    off = app.get_model_settings("Anthropic", "adaptive-model")
    high = app.get_model_settings("Anthropic", "adaptive-model", effort="high")
    always_on = app.get_model_settings("Anthropic", "always-on-model")

    assert off["effort_options"] == ["none", "low", "high"]
    assert off["effort_value"] == "none"
    assert off["thinking_value"] is False
    assert off["temperature_interactive"] is True
    assert high["thinking_value"] is True
    assert high["temperature_value"] == 1.0
    assert high["temperature_interactive"] is False
    assert always_on["effort_options"] == ["low", "high"]
    assert always_on["thinking_value"] is True


def test_effort_sync_locks_fixed_temperature_and_keeps_slider_mounted(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {
                    "adaptive-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "disabled",
                        "thinking_fixed_temperature": 1.0,
                    },
                    "no-temperature-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "lowest_effort",
                        "temperature_supported": False,
                    },
                }
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    _, temperature, thinking, effort, _ = app.sync_controls_for_effort(
        "Anthropic", "adaptive-model", False, "high", 0.4, "effort"
    )
    _, off_temperature, off_thinking, _, _ = app.sync_controls_for_effort(
        "Anthropic", "adaptive-model", True, "none", 0.4, "effort"
    )
    _, hidden_temperature, _, _, _ = app.sync_controls_for_model(
        "Anthropic", "no-temperature-model", False, "none", 0.4, "effort"
    )

    assert temperature == {"visible": True, "value": 1.0, "interactive": False}
    assert thinking["value"] is True
    assert effort["value"] == "high"
    # Unlocking restores the user's requested temperature, not a default.
    assert off_temperature == {"visible": True, "value": 0.4, "interactive": True}
    assert off_thinking["value"] is False
    assert hidden_temperature["visible"] == "hidden"


def test_model_switch_keeps_temperature_effort_and_toggle_choices(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Google": {
                    "effort-a": {
                        "extended_thinking": True,
                        "effort_options": ["low", "medium", "high"],
                        "thinking_off": "lowest_effort",
                    },
                    "effort-b": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "lowest_effort",
                    },
                    "toggle-a": {
                        "extended_thinking": True,
                        "thinking_off": "disabled",
                    },
                    "toggle-b": {
                        "extended_thinking": True,
                        "thinking_off": "disabled",
                    },
                }
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    _, temperature, _, effort, control = app.sync_controls_for_model(
        "Google", "effort-b", True, "high", 0.6, "effort"
    )
    _, _, _, missing_effort, _ = app.sync_controls_for_model(
        "Google", "effort-b", True, "medium", 0.6, "effort"
    )
    _, _, toggle_kept, _, _ = app.sync_controls_for_model(
        "Google", "toggle-b", True, "low", 0.6, "toggle"
    )
    # An effort model's hidden checkbox is True; it must not switch reasoning on.
    _, _, toggle_from_effort, _, _ = app.sync_controls_for_model(
        "Google", "toggle-a", True, "high", 0.6, "effort"
    )

    assert temperature["value"] == 0.6
    assert effort["value"] == "high"
    assert control == "effort"
    assert missing_effort["value"] == "low"
    assert toggle_kept["value"] is True
    assert toggle_from_effort["value"] is False


def test_provider_none_effort_keeps_thinking_on_for_core(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "openai-model": {
                        "extended_thinking": True,
                        "effort_options": ["none", "low"],
                        "thinking_off": "disabled",
                        "temperature_supported": False,
                    }
                }
            }
        },
    )

    settings = app.get_model_settings("OpenAI", "openai-model", effort="none")

    # Core's own "none" is an effort level, sent with use_thinking=True.
    assert settings["effort_value"] == "none"
    assert settings["thinking_value"] is True


def test_model_settings_inspect_only_the_selected_ollama_model(monkeypatch):
    inspected = []
    monkeypatch.setattr(app.ollama_api, "get_model_list", lambda: ["gpt-oss", "qwen3"])

    def get_model_status(model):
        inspected.append(model)
        return {
            "model_capabilities": {
                "extended_thinking": True,
                "effort_options": ["low", "medium", "high"],
                "temperature_supported": True,
                "thinking_fixed_temperature": None,
                "thinking_off": "lowest_effort",
            }
        }

    monkeypatch.setattr(app.ollama_api, "get_model_status", get_model_status)

    settings = app.get_model_settings("Ollama", "gpt-oss")

    assert inspected == ["gpt-oss"]
    assert settings["show_effort"] is True
    assert settings["effort_options"] == ["low", "medium", "high"]
    assert settings["thinking_value"] is True
    assert settings["show_temperature"] is True


def test_model_settings_show_no_reasoning_for_uninspectable_ollama_model(
    monkeypatch,
):
    monkeypatch.setattr(app.ollama_api, "get_model_list", lambda: ["llama3"])
    monkeypatch.setattr(
        app.ollama_api,
        "get_model_status",
        lambda model: {"model_capabilities": None},
    )

    settings = app.get_model_settings("Ollama", "llama3")

    assert settings["show_thinking"] is False
    assert settings["show_effort"] is False
    assert settings["thinking_value"] is False
    assert settings["show_temperature"] is True


def test_get_providers_does_not_inspect_every_ollama_model(monkeypatch):
    monkeypatch.setattr(app.ollama_api, "get_model_list", lambda: ["llama3"])
    monkeypatch.setattr(
        app.ollama_api,
        "get_ollama_status",
        lambda: (_ for _ in ()).throw(AssertionError("must not inspect models")),
    )

    assert app.get_providers()[-1] == "Ollama"


def test_context_size_is_only_shown_for_ollama(monkeypatch):
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    assert app.sync_context_size_for_provider("Ollama") == {"visible": True}
    # The selection is kept; run_loop only sends it for Ollama.
    assert app.sync_context_size_for_provider("OpenAI") == {"visible": False}


def test_run_loop_passes_ollama_context_size_to_core(monkeypatch, tmp_path):
    captured = {}
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: None)
    monkeypatch.setattr(app, "MidiFile", lambda path: "midi")
    monkeypatch.setattr(app, "visualize_midi_plotly", lambda midi: "viz")

    class FakeEngine:
        def __init__(self, config):
            pass

        def generate(self, request, progress_callback=None):
            captured["request"] = request
            return SimpleNamespace(
                midi_path=str(midi_path),
                audio_path=None,
                cost=0,
                generation_id="fixed_id",
                metadata=SimpleNamespace(soundfont=None),
                warnings=[],
            )

    monkeypatch.setattr(app, "LoopGenerationEngine", FakeEngine)

    list(
        app.run_loop(
            key="C",
            scale="Major",
            description="local loop",
            temp=0.3,
            model_choice="llama3",
            use_thinking=False,
            effort="low",
            soundfont_choice=None,
            openai_key="",
            gemini_key="",
            claude_key="",
            ollama_num_ctx=8192.0,
            provider="Ollama",
        )
    )

    assert captured["request"].ollama_num_ctx == 8192
    assert isinstance(captured["request"].ollama_num_ctx, int)

    list(
        app.run_loop(
            key="C",
            scale="Major",
            description="cloud loop",
            temp=0.3,
            model_choice="gpt-test",
            use_thinking=False,
            effort="low",
            soundfont_choice=None,
            openai_key="",
            gemini_key="",
            claude_key="",
            ollama_num_ctx=8192,
            provider="OpenAI",
        )
    )

    assert captured["request"].ollama_num_ctx is None


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
                        "thinking_off": "lowest_effort",
                        "temperature_supported": False,
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
            use_thinking=True,
            effort="high",
        )
    )

    assert updates.key == {"value": "F#/Gb"}
    assert updates.scale == {"value": "minor"}
    assert updates.description == {"value": "restored prompt"}
    assert updates.provider["value"] == "OpenAI"
    assert updates.model["value"] == "reasoning-model"
    assert updates.temperature == {
        "visible": "hidden",
        "value": 0.7,
        "interactive": True,
    }
    assert updates.use_thinking == {"visible": False, "value": True}
    assert updates.effort == {
        "choices": ["low", "medium", "high"],
        "value": "high",
        "visible": True,
    }
    assert updates.warnings == ()


def test_history_controls_restore_thinking_off_effort_record_as_lowest_level(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "reasoning-model": {
                        "extended_thinking": True,
                        "effort_options": ["none", "low", "high"],
                        "thinking_off": "disabled",
                    }
                }
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="older generation",
            provider="OpenAI",
            model="reasoning-model",
            temperature=0.1,
            use_thinking=False,
            effort="high",
        )
    )

    assert updates.use_thinking == {"visible": False, "value": True}
    assert updates.effort["value"] == "none"
    assert updates.requested_temperature == 0.1
    assert updates.reasoning_control == "effort"
    assert updates.warnings == ()


def test_history_controls_warn_and_preserve_key_for_invalid_saved_value(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "known-model": {
                        "extended_thinking": False,
                        "effort_options": [],
                    }
                }
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="not-a-key",
            scale="Major",
            prompt="restored prompt",
            provider="OpenAI",
            model="known-model",
            temperature=0.5,
            use_thinking=False,
            effort="low",
        )
    )

    assert updates.key == {}
    assert updates.scale == {"value": "Major"}
    assert updates.description == {"value": "restored prompt"}
    assert updates.warnings == ("Unavailable key: 'not-a-key'.",)


def test_history_controls_restore_known_toggle_model_exactly(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {
                    "toggle-model": {
                        "extended_thinking": True,
                        "thinking_off": "disabled",
                        "thinking_fixed_temperature": 1.0,
                    }
                }
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

    assert updates.temperature == {
        "visible": True,
        "value": 1.0,
        "interactive": False,
    }
    assert updates.use_thinking == {"visible": True, "value": True}
    assert updates.effort == {"choices": ["low"], "value": "low", "visible": False}
    assert updates.warnings == ()


def test_history_controls_use_defaults_and_warn_for_legacy_reasoning(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "Anthropic": {
                    "toggle-model": {"extended_thinking": True, "effort_options": []}
                }
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


def test_history_controls_restore_installed_ollama_without_false_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        app, "get_model_info", lambda: {"models": {"OpenAI": {"current-model": {}}}}
    )
    monkeypatch.setattr(
        app.ollama_api,
        "get_ollama_status",
        lambda request_timeout: {
            "available": True,
            "models": ["gemma4:e4b", "other:latest"],
        },
    )
    monkeypatch.setattr(
        app.ollama_api, "get_model_list", lambda: ["gemma4:e4b", "other:latest"]
    )
    monkeypatch.setattr(
        app.ollama_api,
        "get_model_status",
        lambda model: {
            "model_capabilities": {
                "extended_thinking": True,
                "effort_options": ["low", "medium", "high"],
            }
        },
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="local history",
            provider="Ollama",
            model="gemma4:e4b",
            temperature=0.5,
            use_thinking=True,
            effort="medium",
        )
    )

    assert updates.provider == {"choices": ["OpenAI", "Ollama"], "value": "Ollama"}
    assert updates.model == {
        "choices": [("gemma4:e4b", "gemma4:e4b"), ("other:latest", "other:latest")],
        "value": "gemma4:e4b",
    }
    assert updates.temperature == {"visible": True, "value": 0.5, "interactive": True}
    assert updates.use_thinking == {"visible": False, "value": True}
    assert updates.effort == {
        "choices": ["low", "medium", "high"],
        "value": "medium",
        "visible": True,
    }
    assert updates.warnings == ()


def test_history_controls_mark_missing_ollama_model_only(monkeypatch):
    monkeypatch.setattr(
        app, "get_model_info", lambda: {"models": {"OpenAI": {"current-model": {}}}}
    )
    monkeypatch.setattr(
        app.ollama_api,
        "get_ollama_status",
        lambda request_timeout: {"available": True, "models": ["other:latest"]},
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="local",
            provider="Ollama",
            model="missing:latest",
            temperature=0.5,
            use_thinking=False,
            effort="low",
        )
    )

    assert updates.provider["choices"][-1] == "Ollama"
    assert updates.model["choices"][-1] == (
        "missing:latest (unavailable)",
        "missing:latest",
    )
    assert updates.warnings == ("Unavailable selection: Ollama / missing:latest.",)


def test_history_controls_preserve_ollama_when_service_unreachable(monkeypatch):
    monkeypatch.setattr(
        app, "get_model_info", lambda: {"models": {"OpenAI": {"current-model": {}}}}
    )
    monkeypatch.setattr(
        app.ollama_api,
        "get_ollama_status",
        lambda request_timeout: {"available": False, "models": []},
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    updates = app.get_history_control_updates(
        SimpleNamespace(
            key="C",
            scale="Major",
            prompt="local",
            provider="Ollama",
            model="gemma4:e4b",
            temperature=0.5,
            use_thinking=False,
            effort="low",
        )
    )

    assert updates.provider["choices"][-1] == ("Ollama (unavailable)", "Ollama")
    assert updates.model["choices"] == [("gemma4:e4b (unavailable)", "gemma4:e4b")]
    assert updates.warnings == ("Unavailable selection: Ollama / gemma4:e4b.",)


def test_history_store_uses_the_app_retention_policy():
    assert app.HISTORY_STORE.max_generations == app.MAX_HISTORY_GENERATIONS


def test_prompt_override_uses_the_app_data_directory(monkeypatch, tmp_path):
    override_path = tmp_path / "Prompts" / "loop gen.txt"
    monkeypatch.setattr(app, "PROMPT_OVERRIDE_PATH", override_path)

    assert app.load_app_prompt_override() is None
    assert app.save_prompts("standalone override").startswith(
        "Prompts saved successfully"
    )
    assert override_path.read_text(encoding="utf-8") == "standalone override"
    assert app.load_app_prompt_override() == "standalone override"


def test_rerender_current_audio_skips_existing_matching_soundfont(
    monkeypatch, tmp_path
):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")

    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")

    def fail_render(*args, **kwargs):
        raise AssertionError(
            "midi_to_mp3 should not be called when the audio is already current"
        )

    monkeypatch.setattr(app, "midi_to_mp3", fail_render)

    rerendered_audio_path, status, saved_soundfont, current_audio_path = (
        app.rerender_current_audio(
            str(midi_path),
            "custom.sf2",
            "custom.sf2",
            "gen_1",
            str(audio_path),
        )
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

    rerendered_audio_path, status, saved_soundfont, current_audio_path = (
        app.rerender_current_audio(
            str(midi_path),
            "custom.sf2",
            "old.sf2",
            "gen_1",
            None,
        )
    )

    assert rerendered_audio_path == str(tmp_path / "saved-loop.mp3")
    assert status == "Rendered audio with custom.sf2."
    assert saved_soundfont == "custom.sf2"
    assert current_audio_path == str(tmp_path / "saved-loop.mp3")


def test_rerender_current_audio_reports_core_rendering_error(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    current_audio = _write_binary_file(tmp_path / "current.mp3")

    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "custom.sf2")

    def fail_render(*args, **kwargs):
        raise AudioRenderingError("FluidSynth timed out")

    monkeypatch.setattr(app, "midi_to_mp3", fail_render)

    result = app.rerender_current_audio(
        str(midi_path),
        "custom.sf2",
        "old.sf2",
        "gen_1",
        str(current_audio),
    )

    assert result == (
        str(current_audio),
        "FluidSynth timed out",
        "old.sf2",
        str(current_audio),
    )


def test_load_history_item_warns_when_saved_soundfont_is_missing(monkeypatch, tmp_path):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")
    google_model = next(iter(app.get_model_info()["models"]["Google"]))

    monkeypatch.setattr(
        app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"]
    )
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
            key="C#",
            scale="minor",
            prompt="saved prompt",
            provider="Google",
            model=google_model,
            temperature=0.6,
            use_thinking=False,
            effort="low",
        ),
    )
    monkeypatch.setattr(app, "MidiFile", lambda path: object())
    monkeypatch.setattr(app, "visualize_midi_plotly", lambda midi: "viz")
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (True, None)
    )
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
        requested_temperature,
        reasoning_control,
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
    assert key_update["value"] == "C#/Db"
    assert scale_update["value"] == "minor"
    assert description_update["value"] == "saved prompt"
    assert provider_update["value"] == "Google"
    assert model_update["value"] == google_model
    assert temperature_update["value"] == 0.6
    assert thinking_update["value"] is True
    assert effort_update["value"] == effort_update["choices"][0]
    assert requested_temperature == 0.6
    assert reasoning_control == "effort"


def test_load_history_item_error_paths_preserve_parameter_controls(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)
    monkeypatch.setattr(app, "get_soundfont_choices", list)
    monkeypatch.setattr(app, "get_default_soundfont", lambda: None)
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (False, None)
    )

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
        assert len(result) == 19
        assert result[-10:] == ({},) * 10


def test_refresh_soundfont_controls_updates_dropdown_choices(monkeypatch):
    midi_path = _write_binary_file(Path("active.mid"))

    monkeypatch.setattr(
        app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"]
    )
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (True, None)
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    try:
        dropdown_update, rerender_update, status_message = (
            app.refresh_soundfont_controls(
                "new.sf2",
                str(midi_path),
            )
        )

        assert dropdown_update["choices"] == ["FM-Piano1 20190916.sf2", "new.sf2"]
        assert dropdown_update["value"] == "new.sf2"
        assert rerender_update["interactive"] is True
        assert status_message == "Found 2 SoundFonts. Selected new.sf2."
    finally:
        midi_path.unlink(missing_ok=True)


def test_refresh_soundfont_controls_prefers_dependency_status_message(monkeypatch):
    monkeypatch.setattr(
        app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"]
    )
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(
        app,
        "is_playback_available",
        lambda soundfont_name=None: (
            False,
            "FluidSynth is not installed or not in PATH",
        ),
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
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (True, None)
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    rerender_update = app.get_rerender_button_update("new.sf2", None)

    assert rerender_update["interactive"] is False


def test_history_choices_show_context_in_newest_first_order(monkeypatch):
    from datetime import datetime, timezone

    entries = [
        SimpleNamespace(
            id=identifier,
            timestamp=datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc),
            prompt="similar prompt " + identifier,
            key="C",
            scale="Major",
            model="model-a",
            provider="OpenAI",
            use_thinking=None,
            effort=None,
        )
        for identifier, day in [("new", 2), ("old", 1)]
    ]
    monkeypatch.setattr(app, "load_history", lambda: entries)
    choices = app.get_history_choices()

    assert [value for _, value in choices] == ["new", "old"]
    assert choices[0][0] == ("C Major · Jan 02, 12:00 PM\nmodel-a\nsimilar prompt new")


def test_history_labels_keep_prompt_on_one_bounded_line(monkeypatch):
    from datetime import datetime, timezone

    entry = SimpleNamespace(
        id="gen",
        timestamp=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        prompt="first line\n\nsecond\tline " + "x" * 200,
        key="C",
        scale="Major",
        model="model-a",
        provider="OpenAI",
        use_thinking=None,
        effort=None,
    )
    monkeypatch.setattr(app, "load_history", lambda: [entry])

    _, model, prompt = app.get_history_choices()[0][0].split("\n")

    assert model == "model-a"
    assert prompt.startswith("first line second line x")
    assert prompt.endswith("...")
    assert len(prompt) == app.HISTORY_PROMPT_MAX_CHARS + len("...")


def test_refresh_history_preserves_only_existing_selection(monkeypatch):
    monkeypatch.setattr(app, "get_history_choices", lambda: [("first", "gen_1")])
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    assert app.refresh_history("gen_1")["value"] == "gen_1"
    assert app.refresh_history("missing")["value"] is None


def test_delete_requires_confirmation_and_clears_loaded_artifacts(
    monkeypatch, tmp_path
):
    midi_path = _write_binary_file(tmp_path / "loop.mid")
    audio_path = _write_binary_file(tmp_path / "loop.mp3")
    deleted = []
    monkeypatch.setattr(
        app, "delete_generation", lambda gen_id: deleted.append(gen_id) or True
    )
    monkeypatch.setattr(app, "get_history_choices", lambda: [("remaining", "gen_2")])
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (True, None)
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    assert app.show_delete_confirmation("gen_1")[:2] == (
        {"visible": False},
        {"visible": True},
    )
    assert app.cancel_delete_confirmation()[:2] == (
        {"visible": True},
        {"visible": False},
    )
    assert app.hide_delete_confirmation() == (
        {"visible": True},
        {"visible": False},
    )
    assert deleted == []
    result = app.delete_history_item(
        "gen_1",
        current_generation_id="gen_1",
        soundfont_choice="new.sf2",
        midi_path=str(midi_path),
        current_saved_soundfont="old.sf2",
        current_audio_path=str(audio_path),
    )

    assert deleted == ["gen_1"]
    assert result[0] == {"choices": [("remaining", "gen_2")], "value": None}
    assert result[1] == "Deleted generation"
    assert result[2:4] == ({"visible": True}, {"visible": False})
    assert result[4:10] == (None, None, None, None, None, None)
    assert result[10]["interactive"] is False


def test_history_empty_and_missing_selection(monkeypatch):
    monkeypatch.setattr(app, "load_history", list)
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    assert app.get_history_choices() == []
    assert app.refresh_history("missing")["value"] is None
    assert app.show_delete_confirmation(None)[:2] == (
        {"visible": True},
        {"visible": False},
    )
    assert app.load_history_item(None)[4] == "No generation selected"


def test_history_choices_pair_model_with_reasoning_details(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_model_info",
        lambda: {
            "models": {
                "OpenAI": {
                    "effort-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "medium", "high", "xhigh"],
                    },
                    "effort-off-model": {
                        "extended_thinking": True,
                        "effort_options": ["none", "low", "high"],
                    },
                    "adaptive-off-model": {
                        "extended_thinking": True,
                        "effort_options": ["low", "high"],
                        "thinking_off": "disabled",
                    },
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
                use_thinking=True,
                effort="xhigh",
            ),
            SimpleNamespace(
                **history_defaults,
                id="effort-off",
                provider="OpenAI",
                model="effort-off-model",
                use_thinking=False,
                effort="high",
            ),
            SimpleNamespace(
                **history_defaults,
                id="adaptive-off",
                provider="OpenAI",
                model="adaptive-off-model",
                use_thinking=False,
                effort="none",
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

    rendered_history = " ".join(label for label, _ in app.get_history_choices())

    assert "effort-model (xhigh)" in rendered_history
    assert "effort-off-model (none)" in rendered_history
    assert "adaptive-off-model (none)" in rendered_history
    assert "toggle-model (reasoning)" in rendered_history
    assert "legacy-model (" not in rendered_history
    assert "toggle-off-model (" not in rendered_history


def test_refresh_soundfont_controls_stays_disabled_after_active_delete(monkeypatch):
    monkeypatch.setattr(
        app, "get_soundfont_choices", lambda: ["FM-Piano1 20190916.sf2", "new.sf2"]
    )
    monkeypatch.setattr(app, "get_selected_soundfont", lambda choice=None: "new.sf2")
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont_name=None: (True, None)
    )
    monkeypatch.setattr(app.gr, "update", lambda **kwargs: kwargs)

    _, rerender_update, _ = app.refresh_soundfont_controls("new.sf2", None)

    assert rerender_update["interactive"] is False


def _history_sidebar(demo):
    return next(
        component
        for component in demo.config["components"]
        if component["type"] == "sidebar"
    )


def test_history_sidebar_starts_collapsed_on_the_right():
    demo = app.create_demo(playback_status=(True, None))
    sidebar = _history_sidebar(demo)

    assert sidebar["props"]["open"] is False
    assert sidebar["props"]["position"] == "right"
    assert sidebar["props"]["width"] == app.HISTORY_SIDEBAR_WIDTH
    assert not any(
        component["type"] == "button" and component["props"].get("value") == "History"
        for component in demo.config["components"]
    )


def test_opening_history_reloads_entries_and_keeps_a_valid_selection():
    demo = app.create_demo(playback_status=(True, None))
    sidebar_id = _history_sidebar(demo)["id"]
    selector = next(
        dependency["inputs"][0]
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] == "load_history_item"
    )
    reload_dependency = next(
        dependency
        for dependency in demo.config["dependencies"]
        if (sidebar_id, "expand") in dependency["targets"] and not dependency["js"]
    )

    assert reload_dependency["api_name"] == "refresh_history"
    assert reload_dependency["targets"] == [(sidebar_id, "expand")]
    assert reload_dependency["inputs"] == [selector]
    assert reload_dependency["outputs"] == [selector]


def test_history_sidebar_resizes_piano_roll_when_opened_or_closed():
    demo = app.create_demo(playback_status=(True, None))
    sidebar_id = _history_sidebar(demo)["id"]
    resize_dependency = next(
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["js"] == app.PIANO_ROLL_RESIZE_JS
    )

    piano_roll = next(
        component
        for component in demo.config["components"]
        if component["props"].get("elem_id") == "piano-roll"
    )

    assert piano_roll["type"] == "plot"
    assert resize_dependency["targets"] == [
        (sidebar_id, "expand"),
        (sidebar_id, "collapse"),
    ]
    assert resize_dependency["queue"] is False


def test_audio_playback_loops_generated_audio():
    demo = app.create_demo(playback_status=(True, None))
    audio = next(
        component
        for component in demo.config["components"]
        if component["type"] == "audio"
        and component["props"].get("label") == "Playback"
    )

    assert audio["props"]["loop"] is True


def test_history_sidebar_uses_one_selector_and_confirmed_delete():
    demo = app.create_demo(playback_status=(True, None))
    components = {component["id"]: component for component in demo.config["components"]}
    dependencies = {
        dependency["api_name"]: dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"]
    }
    selector = dependencies["load_history_item"]["inputs"][0]

    assert components[selector]["type"] == "radio"
    assert components[selector]["props"]["label"] == "Recent Generations"
    assert dependencies["show_delete_confirmation"]["inputs"] == [selector]
    actions_id, confirmation_id, _ = dependencies["show_delete_confirmation"]["outputs"]
    assert components[actions_id]["type"] == "row"
    assert components[confirmation_id]["type"] == "row"
    assert components[actions_id]["props"]["visible"] is True
    assert components[confirmation_id]["props"]["visible"] is False
    assert dependencies["hide_delete_confirmation"]["outputs"] == [
        actions_id,
        confirmation_id,
    ]
    # Programmatic list updates (delete, refresh, open) must not reset status.
    assert dependencies["select_history_item"]["targets"] == [(selector, "input")]
    assert dependencies["cancel_delete_confirmation"]["outputs"][:2] == [
        actions_id,
        confirmation_id,
    ]
    assert dependencies["delete_history_item"]["outputs"][2:4] == [
        actions_id,
        confirmation_id,
    ]
    assert dependencies["delete_history_item"]["inputs"][0] == selector
    assert dependencies["refresh_history"]["inputs"] == [selector]
    sidebar_controls = [
        component["props"].get("label") or component["props"].get("value")
        for component in demo.config["components"]
    ]
    assert sidebar_controls.index("Load") < sidebar_controls.index("Recent Generations")
    assert sidebar_controls.index("Delete...") < sidebar_controls.index(
        "Recent Generations"
    )
    assert sidebar_controls.index("History status") < sidebar_controls.index(
        "Recent Generations"
    )
    assert not any(
        component["type"] == "dropdown"
        and component["props"].get("label") == "Select Generation"
        for component in components.values()
    )


def test_history_load_callback_updates_all_parameter_controls_once():
    demo = app.create_demo(playback_status=(True, None))
    dependency = next(
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] == "load_history_item"
    )
    components_by_id = {
        component["id"]: component for component in demo.config["components"]
    }
    restored_labels = [
        components_by_id[component_id]["props"].get("label")
        for component_id in dependency["outputs"][-10:-2]
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


def test_provider_sync_does_not_send_the_stale_model_choice():
    demo = app.create_demo(playback_status=(True, None))
    labels = {
        component["id"]: component["props"].get("label")
        for component in demo.config["components"]
    }
    dependency = next(
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] == "sync_controls_for_provider"
    )

    # Gradio rejects a dropdown value missing from its current choices.
    assert "Model" not in [labels.get(component) for component in dependency["inputs"]]


def test_model_sync_callbacks_only_run_for_user_input():
    demo = app.create_demo(playback_status=(True, None))
    sync_api_names = {
        "sync_controls_for_provider",
        "sync_controls_for_model",
        "sync_controls_for_thinking",
        "sync_controls_for_effort",
    }
    sync_dependencies = [
        dependency
        for dependency in demo.config["dependencies"]
        if dependency["api_name"] in sync_api_names
    ]

    assert {
        dependency["api_name"] for dependency in sync_dependencies
    } == sync_api_names
    assert all(
        dependency["targets"][0][1] == "input" for dependency in sync_dependencies
    )


def test_main_allows_gradio_to_serve_generation_history(monkeypatch, tmp_path):
    artifact_root = tmp_path / "app-data" / "generations"
    launched_with = {}

    class FakeDemo:
        def launch(self, **kwargs):
            launched_with.update(kwargs)

    monkeypatch.setattr(
        app, "HISTORY_STORE", SimpleNamespace(artifact_root=str(artifact_root))
    )
    monkeypatch.setattr(app, "get_selected_soundfont", lambda: None)
    monkeypatch.setattr(
        app, "is_playback_available", lambda soundfont=None: (True, None)
    )
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
    assert (
        app._resolve_app_soundfont_dir()
        == tmp_path / ".conductor" / "main" / "soundfonts"
    )


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
