# 1C Harness

AI harness for safe, model-agnostic development and automation of 1C:Enterprise.

The project is designed as a bridge between an LLM and 1C. The model should not edit random XML or get unrestricted access to a production infobase. Instead, the harness exposes a small set of explicit tools for reading exported configuration sources, changing modules, validating changes through Designer, inspecting diffs and rolling back.

## Current MVP direction

The first milestone implements the closed loop:

`read -> plan -> patch -> check -> diff -> rollback/apply`

Model providers are isolated behind one interface. GigaChat 3 Ultra is the default provider, while OpenAI-compatible APIs, DeepSeek and Anthropic are supported through adapters/configuration without coupling the agent to one vendor.

## Planned 1C capabilities

- dump configuration to files;
- search and read BSL/XML sources;
- safely patch files inside a workspace;
- load configuration from files;
- run `/CheckModules` and later `/CheckConfig`;
- update DB configuration only with an explicit write/apply action;
- show git diff and restore changes;
- later: Test Client/Test Manager, COMConnector and high-level metadata operations.

## Safety model

- production-changing operations are never implicit;
- file access is sandboxed to the configured workspace;
- commands have timeouts and capture logs;
- credentials are read from environment variables only;
- `.env` is ignored by git;
- destructive/apply operations will require an explicit flag;
- source changes are expected to be version-controlled.

## Quick start

Requires Python 3.11+ and a local 1C installation for Designer commands.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy .env.example .env
```

Set at minimum:

```env
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
GIGACHAT_CREDENTIALS=...
GIGACHAT_SCOPE=GIGACHAT_API_PERS
ONEC_EXE=C:\Program Files\1cv8\8.3.xx.xxxx\bin\1cv8.exe
ONEC_IB_CONNECTION=/F C:\path\to\infobase
ONEC_WORKSPACE=C:\path\to\exported-config
```

Then verify configuration and provider wiring:

```bash
onec-harness doctor
onec-harness ask "Кратко опиши роль этого harness"
```

Designer commands are exposed separately so they remain auditable:

```bash
onec-harness dump-config
onec-harness check-modules
onec-harness diff
```

## Providers

### GigaChat

Default model: `GigaChat-3-Ultra`. Authentication uses the GigaChat authorization key to obtain a short-lived access token and then calls `https://api.giga.chat/v1/chat/completions`.

### OpenAI / DeepSeek / compatible APIs

Use the generic OpenAI-compatible adapter by setting a base URL, API key and model name.

### Anthropic

Uses a dedicated Messages API adapter because Anthropic is not OpenAI-compatible.

## Roadmap

1. Provider abstraction + GigaChat Ultra.
2. Safe filesystem workspace + git diff/rollback.
3. 1C Designer adapter: dump/load/check.
4. Agent action loop with allowlisted tools.
5. BSL-aware symbol/module index.
6. 1C Test Client/Test Manager adapter.
7. COMConnector runtime adapter.
8. High-level semantic tools such as `create_catalog()` and `create_document()`.
9. MCP server so Codex/Claude/ChatGPT-compatible agents can use the same 1C tools.

## Status

Early prototype. Do not point it at a production infobase yet.
