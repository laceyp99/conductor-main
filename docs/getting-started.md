# Getting Started

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands from the `conductor-main` project directory:

```powershell
uv sync --locked
uv run --locked conductor-main
```

`uv sync` creates the project `.venv` and installs the locked `conductor-core` dependency automatically. Activating the environment is not required.

The app opens at `http://127.0.0.1:7860/`.

## Provider Setup

API keys can be entered in the app's **API Keys** accordion. Values entered there are passed to Core for the current generation and are not written into generation metadata.

Core also recognizes these environment variables:

```powershell
$env:OPENAI_API_KEY = "..."
$env:GEMINI_API_KEY = "..."
$env:ANTHROPIC_API_KEY = "..."
$env:OLLAMA_API_HOST_ADDRESS = "http://localhost:11434"
```

Ollama appears as a provider only when its configured server is reachable and reports installed models. Cloud provider usage may incur charges.
