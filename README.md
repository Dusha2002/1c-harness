# 1C Harness

Model-agnostic AI harness and desktop review app for safe development and automation of **1C:Enterprise**.

The target is a local **“Codex / Claude Code for 1C”**: an agent that understands 1C metadata and BSL, edits configuration sources, validates them in a disposable staging infobase, inspects runtime data through `V83.COMConnector`, and can execute UI scenarios through the standard 1C Test Client/Test Manager stack.

## Architecture

```text
GigaChat / OpenAI / DeepSeek / Anthropic / compatible API
                         |
                    HarnessAgent
                         |
      +------------------+--------------------+
      |                  |                    |
 metadata + BSL      Workspace            Runtime 1C
 semantic API         sandbox             adapters
      |                  |                    |
 objects/attributes  snapshot/diff      Designer staging
 enums/tabulars      accept/reject      COMConnector
                                        Test Client/Manager
```

The model receives an allowlisted tool protocol. It never gets arbitrary shell access or direct SQL access to the 1C database.

## What works now

- provider abstraction for GigaChat 3 Ultra, OpenAI, DeepSeek, Anthropic and generic OpenAI-compatible APIs;
- safe BSL/XML workspace, metadata and symbol indexes, snapshots, diff and rollback;
- semantic mutations for catalogs, documents, enumerations, attributes, tabular sections and modules;
- staging-only `LoadConfigFromFiles -> CheckModules -> CheckConfig` validation loop;
- COMConnector runtime reads, with runtime writes behind a double opt-in;
- Test Client/Test Manager launchers and a logical UI action compiler;
- generated Test Manager runner EPF: scenario JSON -> BSL -> external processor -> `/Execute` -> `OK/FAILED` result;
- Tauri + React desktop review app with Monaco diff and real snapshot rejection.

## E2E UI testing

Example scenario:

```json
[
  {"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},
  {"action":"wait_form","title":"Оборудование*"},
  {"action":"set_field","name":"Наименование","value":"Тест"},
  {"action":"click_button","title":"Записать и закрыть"}
]
```

Compile only:

```powershell
onec-harness compile-test-scenario scenario.json -o generated-test.bsl
```

Build/execute the generated runner:

```powershell
onec-harness run-test-scenario scenario.json
onec-harness run-test-scenario scenario.json --execute --yes
```

The agent gets `run_ui_test` when started with `--ui-test`. If the current run changed source or metadata, UI execution is blocked until `stage_config` succeeds with `update_db=true`.

## Safety

- autonomous validation never loads into the primary infobase;
- staging is required for `--check` and generated E2E runner compilation;
- Test Client targets an explicit test connection or staging, never primary by fallback;
- generated E2E runner EPFs are compiled through the staging Designer connection;
- the model never receives unrestricted shell access;
- workspace paths cannot escape `ONEC_WORKSPACE`;
- every source/metadata mutation receives a snapshot;
- COM writes require two explicit opt-ins;
- primary configuration load remains an explicit `--execute --yes` action and can be preceded by `.dt` backup.

## Quick start on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[windows,dev]"
copy .env.example .env
```

```env
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
GIGACHAT_CREDENTIALS=...
ONEC_EXE=C:\Program Files\1cv8\8.3.xx.xxxx\bin\1cv8.exe
ONEC_IB_CONNECTION=/F "C:\1c\dev"
ONEC_STAGING_IB_CONNECTION=/F "C:\1c\harness-staging"
ONEC_WORKSPACE=C:\projects\my-config-export
ONEC_TEST_MANAGER_CONNECTION=/F "C:\1c\test-manager"
```

Examples:

```powershell
onec-harness doctor
onec-harness agent "Исправь проверку остатков" --write --check
onec-harness agent "Исправь форму и проверь её" --write --check --ui-test
onec-harness create-catalog Оборудование --yes
onec-harness create-enum Статусы --values '["Новый","Закрыт"]' --yes
onec-harness add-tabular-section document Заявка Товары --columns '[{"name":"Товар","value_type":"CatalogRef.Товары"}]' --yes
onec-harness stage-config --execute --yes --update-db
```

## Desktop

```powershell
cd desktop
npm install
npm run tauri dev
```

The desktop app keeps reviewed changes in the local workspace on **Принять** and restores the run snapshots on **Отклонить**. Primary 1C apply is separate.

## Roadmap to v1

1. Validate and harden the generated EPF/Test Manager path on a real Windows + 1C host.
2. Add information/accumulation-register and managed-form semantic builders, then extension-aware operations.
3. Expand the UI-test DSL with table rows, choices, dialogs, screenshots/log artifacts and multi-client scenarios.
4. Add richer runtime/server-function contracts.
5. Expose the safe tool registry over MCP.
6. Add streaming desktop tool events, multi-file diff tabs and test-result cards.

## Status

Alpha prototype. Linux CI validates Python contracts/tests and the desktop TypeScript/Vite build; actual COM and Test Manager execution requires a Windows host with 1C installed. Use disposable staging/test infobases.
