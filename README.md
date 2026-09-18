# 1C Harness

Model-agnostic AI harness and desktop review app for safe development and automation of **1C:Enterprise**.

The target is a local **“Codex / Claude Code for 1C”**: an agent that understands metadata and BSL, edits exported sources, validates changes in a disposable staging infobase, inspects runtime data through `V83.COMConnector`, and verifies UI behavior through the standard Test Client/Test Manager stack.

## v0.4 desktop workflow

**Development preview, not a production certification.** Automated tests and installer builds cannot verify Designer,
COM and Test Manager behavior without a licensed Windows + 1C installation. Target: 1C 8.3 XML source exports and BSL.
1C 7.7 and editing the currently open Configurator editor are not implemented.

Download the installer artifact from the latest successful **Windows desktop** run under GitHub Actions.
It bundles the Python bridge; Python, Node and Git are not required on the user's computer.

1. Open **Подключение и модель**, choose the provider, enter its model ID and key.
2. Enter the 1cv8.exe path, primary development infobase, a separate staging copy and an absolute source folder.
   Connections use `/F "C:\1C\dev"` or `/S "server\base"`. Enter credentials in separate fields.
3. Save, then **Проверить модель**. Select existing XML/BSL exports, or use **Выгрузить из 1С** into an empty folder.
4. Describe a task. Tool progress is streamed. The agent reads metadata and sources before proposing changes.
5. Review **all files** in the selector. Accept keeps source changes; reject restores the pre-task bytes, including BOM/CRLF.
6. If staging checks passed, **Применить в 1С…** offers a separate confirmation, creates a `.dt` backup,
   then loads the checked sources and updates the primary database. A failed backup blocks deployment.

A new task is blocked until pending review is resolved. Interrupted/failed sessions remain reviewable after restart.
External source edits block automatic acceptance/rollback. Stop is cooperative: the current model/1C call finishes first.
Unchecking **Проверять в 1С** allows source-only work but disables deployment. The UI never treats configured paths as
proof of a live connection. All initial content is empty; there are no simulated successful checks.

Settings are stored per user; Windows uses DPAPI encryption. Desktop does not enable runtime data writes.
Optional COM connection strings can include credentials and are protected with the rest of the settings.
The code editor and workers are bundled for offline use. Model calls still need network access.

### First real 1C smoke test

Use disposable primary/staging copies, save a backup independently, and start with a small existing BSL module.
Ask the agent to add a harmless message, inspect every changed file, verify actual Designer logs, reject and check
that the bytes were restored. Repeat, accept, then test the explicit deployment and confirm the behavior in 1C.
For forms, separately configure a Test Manager infobase and enable UI-test. Record platform/configuration versions,
Designer logs and the session result when reporting a failure. These native tests are still required before production use.

## v0.3 capabilities

- GigaChat 3 Ultra, OpenAI, DeepSeek, Anthropic and generic OpenAI-compatible providers;
- sandboxed BSL/XML workspace, metadata/symbol search, snapshots, diff and rollback;
- semantic builders for catalogs, documents, enumerations, attributes, tabular sections, information/accumulation registers and managed forms;
- managed-form creation plus input fields, commands/buttons and generated form handlers;
- configuration-extension workflow: dump sources, borrow base objects, add `Перед/После/Вместо` method interception, stage/check and build `.cfe`;
- validation gates per changed scope: base configuration and every changed extension must independently pass staging, `CheckModules` and `CheckConfig`;
- read-only COMConnector queries, with runtime writes behind a separate double opt-in;
- executable E2E path: JSON scenario -> BSL -> temporary EPF -> Test Client/Test Manager -> `OK/FAILED`;
- stdio MCP server exposing the same safe read/semantic/staging tools to external agents;
- Tauri + React desktop review app with Monaco diff and snapshot rejection.

The model never receives arbitrary shell access or direct SQL access to the 1C database.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[windows,dev]"
copy .env.example .env
```

Minimum useful settings:

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

Run the agent:

```powershell
onec-harness doctor
onec-harness agent "Исправь проверку остатков" --write --check
onec-harness agent "Исправь форму и проверь её" --write --check --ui-test
```

## Managed forms

The semantic agent tools include:

```text
create_managed_form
add_form_input
add_form_command
```

A created form receives its descriptor, `Ext/Form.xml`, form module and optional default-form reference. Form commands can generate a client handler automatically.

## Extensions

Dump an existing extension to `ONEC_WORKSPACE/Extensions/<name>`:

```powershell
onec-harness-extension dump MyExtension --execute
```

The agent can then borrow supported base objects and add safe `Перед`, `После` or `Вместо` method interception. Validate and build it separately from the base configuration:

```powershell
onec-harness-extension stage MyExtension --execute
onec-harness-extension check MyExtension --execute
onec-harness-extension build MyExtension .\build\MyExtension.cfe --execute
```

Autonomous extension validation targets `ONEC_STAGING_IB_CONNECTION`, never the primary infobase.

## E2E UI testing

Example:

```json
[
  {"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},
  {"action":"wait_form","title":"Оборудование*"},
  {"action":"set_field","name":"Наименование","value":"Тест"},
  {"action":"click_button","title":"Записать и закрыть"}
]
```

```powershell
onec-harness compile-test-scenario scenario.json -o generated-test.bsl
onec-harness run-test-scenario scenario.json --execute --yes
```

For changed metadata/code, E2E execution is blocked until each changed scope has been staged with `update_db=true`.

## MCP

Start the stdio server:

```powershell
onec-harness-mcp
```

MCP is read-only by default. Source/metadata mutations require a separate explicit opt-in:

```env
ONEC_MCP_ALLOW_WRITES=true
```

That setting does **not** enable COM/runtime writes. Runtime mutations remain controlled by `ONEC_RUNTIME_ALLOW_WRITES` and caller confirmation.

## Safety model

- primary infobase is never used by the autonomous validation loop;
- every source/metadata mutation is snapshotted before writing;
- workspace paths cannot escape `ONEC_WORKSPACE`;
- base configuration and extensions have independent validation state;
- Test Client targets only explicit test/staging connections;
- COM writes and MCP source writes have separate opt-ins;
- primary configuration apply remains a separate explicit operation.

## Desktop

```powershell
cd desktop
npm install
npm run tauri dev
```

**Принять** keeps reviewed changes in the local workspace; **Отклонить** restores snapshots. Applying to the primary 1C base remains separate.

## Next to v1

1. Real Windows + installed-1C CI/smoke runner for Designer, COM and Test Manager.
2. Richer managed-form semantics: groups, tables, choice fields and event wiring.
3. Broader extension borrowing/metadata merge coverage and platform-version fixtures.
4. Richer UI-test DSL: table rows, choices, dialogs, screenshots and multi-client scenarios.
5. Native Windows + 1C acceptance tests on representative customer configurations.

## Status

**v0.4 development preview.** Linux CI covers Python contracts/tests and the desktop TypeScript/Vite build. Actual Designer/COM/Test Manager execution requires a Windows host with 1C installed. Use disposable staging/test infobases.
