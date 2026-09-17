# 1C Harness

Model-agnostic AI harness for safe development and automation of **1C:Enterprise**.

The long-term goal is a local **“Codex / Claude Code for 1C”**: an agent that understands 1C metadata and BSL, edits configuration sources, validates changes through Designer, can later exercise the application through Test Client/Test Manager and inspect runtime data through COMConnector.

## Current architecture

```text
GigaChat / OpenAI / DeepSeek / Anthropic / compatible API
                         |
                    HarnessAgent
                         |
          +--------------+--------------+
          |              |              |
   metadata + BSL     Workspace      1C Designer
       index           sandbox          CLI
          |              |              |
   objects/symbols   patch/diff      dump/load
                    snapshots        checks
```

The agent uses an allowlisted JSON tool protocol instead of unrestricted shell access.

## What works now

- provider abstraction for GigaChat 3 Ultra, OpenAI, DeepSeek, Anthropic and generic OpenAI-compatible APIs;
- safe source workspace with path-escape protection;
- BSL/XML text search and reading;
- metadata index for common 1C object kinds (catalogs, documents, registers, common modules, reports, processors, etc.);
- BSL procedure/function symbol index;
- exact one-match patches;
- automatic persistent snapshot before each agent patch;
- git diff and explicit rollback;
- `DumpConfigToFiles` / `LoadConfigFromFiles`;
- `/CheckModules` and `/CheckConfig` wrappers;
- agent completion gates: after a patch it must inspect diff, and with `--check` it must obtain a successful 1C validation before finishing;
- JSON CLI output for desktop/MCP integrations;
- Tauri + React desktop UI scaffold with Monaco diff review.

## Safety model

- production-changing operations are never implicit;
- the model never gets arbitrary shell access;
- file access is sandboxed to `ONEC_WORKSPACE`;
- credentials come only from local environment variables / `.env`;
- every patch gets a snapshot id before the file is changed;
- local source review is separate from loading changes into 1C;
- updating DB configuration requires an explicit command and confirmation;
- Designer commands have timeouts and captured logs.

## Python quick start

Requires Python 3.11+ and a local 1C installation for Designer commands.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env
```

Example configuration:

```env
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
GIGACHAT_CREDENTIALS=...
GIGACHAT_SCOPE=GIGACHAT_API_PERS
ONEC_EXE=C:\Program Files\1cv8\8.3.xx.xxxx\bin\1cv8.exe
ONEC_IB_CONNECTION=/F "C:\path\to\infobase"
ONEC_WORKSPACE=C:\path\to\exported-config
```

Useful commands:

```powershell
onec-harness doctor
onec-harness dump-config --execute
onec-harness metadata Заказ
onec-harness symbols Проведение
onec-harness agent "Найди причину ошибки проведения" 
onec-harness agent "Исправь проверку остатков" --write --check
onec-harness diff
onec-harness check-modules --execute
onec-harness check-config --execute
```

Machine-readable integration:

```powershell
onec-harness doctor --json
onec-harness agent "Проверь модуль" --json
```

## Review-first workflow

The intended engineering cycle is:

```text
READ / METADATA
       |
      PLAN
       |
    SNAPSHOT
       |
      PATCH
       |
      DIFF
       |
  CheckModules
       |
 error? ----> inspect 1C log -> fix -> check again
       |
  CheckConfig
       |
 user reviews staged change
       |
 accept / rollback
       |
 load into test configuration
```

The agent already enforces the `patch -> diff -> check` part when checks are enabled. Loading into a test infobase and automated functional testing are intentionally still separate.

## Desktop application

The `desktop/` folder contains a Tauri 2 + React/TypeScript application inspired by an IDE/code-agent review workflow:

- left pane: user chat + visible agent steps;
- right pane: Monaco BSL diff review;
- `Принять / Отклонить` review flow;
- 1C connection status;
- local Tauri bridge to `onec-harness` CLI;
- demo data when no configured 1C installation is available.

Run it with:

```powershell
cd desktop
npm install
npm run tauri dev
```

See `desktop/README.md` for details.

## Provider configuration

### GigaChat

Default model: `GigaChat-3-Ultra`. The provider exchanges the authorization credentials for a short-lived access token and then calls GigaChat chat completions.

### OpenAI / DeepSeek / compatible APIs

Use the generic OpenAI-compatible provider with the desired API base URL, API key and model name.

### Anthropic

Anthropic uses a dedicated Messages API adapter.

## Roadmap to v1

1. **Closed engineering loop** — richer 1C check diagnostics, staged patch sessions and test-infobase apply workflow.
2. **Metadata semantic API** — `get_document`, `get_catalog`, `read_module`, dependency/reference navigation.
3. **High-level mutations** — `create_catalog`, `create_document`, `add_attribute`, `create_register`, `create_extension` without exposing raw XML to the model.
4. **COMConnector runtime adapter** — queries, object retrieval, server function calls and register inspection.
5. **Test Client / Test Manager** — programmatic forms, commands, fields and end-to-end verification.
6. **MCP server** — expose the same safe tools to Codex, Claude, ChatGPT-compatible clients and other agents.
7. **Desktop production flow** — streaming steps, real patch tabs, accept/reject snapshots, test-base apply and test results.

## Status

The project is an active prototype. Use a copy/test infobase; do not point autonomous write/apply flows at production yet.
