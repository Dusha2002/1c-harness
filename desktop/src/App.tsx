import { DiffEditor, type BeforeMount } from "@monaco-editor/react";
import {
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Code2,
  Database,
  FileCode2,
  Menu,
  MessageSquareText,
  Paperclip,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import { demoPatch, demoSteps } from "./mock";
import type { AgentResult, AgentStep, HarnessDoctor, PatchPreview, StepStatus } from "./types";

const registerBsl: BeforeMount = (monaco) => {
  if (monaco.languages.getLanguages().some((language) => language.id === "bsl")) return;

  monaco.languages.register({ id: "bsl" });
  monaco.languages.setMonarchTokensProvider("bsl", {
    ignoreCase: true,
    keywords: [
      "Процедура", "КонецПроцедуры", "Функция", "КонецФункции", "Если", "Тогда", "Иначе", "КонецЕсли",
      "Для", "Каждого", "Из", "Цикл", "КонецЦикла", "Возврат", "Истина", "Ложь", "Неопределено", "Новый",
      "Экспорт", "Перем",
    ],
    tokenizer: {
      root: [
        [/\/\/.*$/, "comment"],
        [/"([^"\\]|\\.)*"/, "string"],
        [/[А-Яа-яЁёA-Za-z_][\wА-Яа-яЁё]*/, { cases: { "@keywords": "keyword", "@default": "identifier" } }],
        [/\d+(\.\d+)?/, "number"],
        [/[=<>+\-*\/]+/, "operator"],
      ],
    },
  });
};

function statusIcon(status: StepStatus) {
  if (status === "done") return <Check size={13} strokeWidth={3} />;
  if (status === "running") return <RefreshCw size={13} className="spin" />;
  if (status === "failed") return <X size={13} strokeWidth={3} />;
  return <Circle size={10} />;
}

function toolLabel(tool: string): string {
  const labels: Record<string, string> = {
    metadata: "Изучены метаданные 1С",
    symbols: "Найдены BSL-процедуры и функции",
    search: "Найдены релевантные фрагменты кода",
    read: "Код модуля прочитан",
    patch: "Подготовлена точечная правка",
    create_catalog: "Создан справочник в метаданных",
    create_document_meta: "Создан документ в метаданных",
    add_attribute: "Добавлен реквизит метаданных",
    ensure_module: "Создан модуль объекта",
    diff: "Diff проверен агентом",
    stage_config: "Изменения загружены в staging 1С",
    check_modules: "CheckModules выполнен",
    check_config: "CheckConfig выполнен",
    runtime_query: "Выполнен read-only запрос к 1С",
    catalog_items: "Прочитаны элементы справочника",
    document_items: "Прочитаны документы",
    register_records: "Прочитаны записи регистра",
    create_catalog_item: "Создан элемент в runtime 1С",
    create_document_record: "Создан документ в runtime 1С",
    ui_test_scenario: "Подготовлен сценарий Test Manager",
    rollback: "Правка откатана из snapshot",
  };
  return labels[tool] ?? tool;
}

function languageFor(path: string): PatchPreview["language"] {
  if (path.toLowerCase().endsWith(".bsl")) return "bsl";
  if (path.toLowerCase().endsWith(".xml")) return "xml";
  return "text";
}

function previewFromResult(result: AgentResult): PatchPreview | null {
  const patch = [...result.steps].reverse().find((step) => step.tool === "patch");
  if (patch) {
    const path = typeof patch.args.path === "string" ? patch.args.path : "Изменённый модуль.bsl";
    const original = typeof patch.args.old === "string" ? patch.args.old : "";
    const modified = typeof patch.args.new === "string" ? patch.args.new : patch.result;
    return {
      id: `live-${Date.now()}`,
      file: path,
      language: languageFor(path),
      original,
      modified,
      snapshotId: result.snapshots.at(-1),
    };
  }

  const diff = [...result.steps].reverse().find((step) => step.tool === "diff");
  if (diff && diff.result && diff.result !== "No changes") {
    const match = /^\+\+\+\s+b\/(.+)$/m.exec(diff.result);
    const path = match?.[1] ?? "workspace.diff";
    return {
      id: `live-${Date.now()}`,
      file: path,
      language: match ? languageFor(path) : "text",
      original: "",
      modified: diff.result,
      snapshotId: result.snapshots.at(-1),
    };
  }
  return null;
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [doctor, setDoctor] = useState<HarnessDoctor | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>(demoSteps);
  const [task, setTask] = useState("");
  const [lastTask, setLastTask] = useState(
    "Проверь проведение документа ЗаказПокупателя и не позволяй проводить его при недостаточном остатке товара.",
  );
  const [running, setRunning] = useState(false);
  const [patchState, setPatchState] = useState<"pending" | "accepted" | "rejected">("pending");
  const [notice, setNotice] = useState<string | null>(null);
  const [activePatch, setActivePatch] = useState<PatchPreview>(demoPatch);
  const [snapshots, setSnapshots] = useState<string[]>([]);
  const [checksOk, setChecksOk] = useState<boolean | null>(true);

  const completed = useMemo(() => steps.filter((step) => step.status === "done").length, [steps]);

  async function connectHarness() {
    setNotice(null);
    try {
      const { getDoctor } = await import("./lib/harness");
      const result = await getDoctor();
      setDoctor(result);
      setConnected(Boolean(result.onec_exe && result.onec_connection));
      const capabilities = [
        result.staging_connection ? "staging" : null,
        result.com_configured ? "COM" : null,
        result.test_client_connection ? "Test Client" : null,
      ].filter(Boolean).join(" · ");
      if (!result.onec_exe || !result.onec_connection) {
        setNotice("Harness найден, но путь к 1С или основная инфобаза ещё не настроены.");
      } else {
        setNotice(capabilities ? `1С подключена. Доступно: ${capabilities}.` : "1С подключена; дополнительные адаптеры ещё не настроены.");
      }
    } catch {
      setConnected(false);
      setNotice("UI запущен в demo-режиме. Запусти через Tauri, чтобы подключить локальный harness.");
    }
  }

  async function submitTask() {
    const trimmed = task.trim();
    if (!trimmed || running) return;
    setRunning(true);
    setNotice(null);
    setLastTask(trimmed);
    setPatchState("pending");
    setSteps([{ id: "live-1", label: "Агент анализирует задачу", status: "running" }]);

    try {
      const { runAgent } = await import("./lib/harness");
      const result = await runAgent(trimmed, { write: true, check: Boolean(doctor?.staging_connection) });
      setSteps(
        result.steps.map((step, index) => ({
          id: `live-${index}`,
          label: toolLabel(step.tool),
          detail: step.result.split("\n")[0].slice(0, 150),
          status: step.result.startsWith("ERROR:") || step.result.includes(": FAILED") ? "failed" as const : "done" as const,
        })),
      );
      setSnapshots(result.snapshots);
      setChecksOk(result.checks_ok);
      const preview = previewFromResult(result);
      if (preview) {
        setActivePatch(preview);
        setPatchState("pending");
      } else {
        setPatchState("accepted");
      }
      setNotice(result.summary);
      setTask("");
    } catch (error) {
      setSteps([{ id: "error", label: "Не удалось запустить локальный harness", detail: String(error), status: "failed" }]);
      setNotice(String(error));
    } finally {
      setRunning(false);
    }
  }

  function acceptPatch() {
    setPatchState("accepted");
    setNotice("Правка оставлена в локальном workspace. Применение к основной 1С остаётся отдельным подтверждаемым действием.");
  }

  async function rejectPatch() {
    if (patchState !== "pending") return;
    try {
      const { restoreSnapshot } = await import("./lib/harness");
      for (const snapshotId of [...snapshots].reverse()) {
        await restoreSnapshot(snapshotId);
      }
      setPatchState("rejected");
      setActivePatch((current) => ({ ...current, modified: current.original }));
      setNotice(snapshots.length ? "Правка отклонена: snapshots восстановлены." : "Правка отклонена.");
      setSnapshots([]);
    } catch (error) {
      setNotice(`Не удалось восстановить snapshot: ${String(error)}`);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <button className="icon-button" aria-label="Меню"><Menu size={19} /></button>
          <div className="brand-mark"><Code2 size={16} /></div>
          <span className="brand-name">1C Harness</span>
        </div>

        <div className="session-title">
          <span>Новая сессия</span>
          <span className="session-separator">/</span>
          <span className="session-time">{doctor?.llm_model ?? "GigaChat 3 Ultra"}</span>
        </div>

        <div className="top-actions">
          <button className={`connect-button ${connected ? "connected" : ""}`} onClick={() => void connectHarness()}>
            <span className="onec-badge">1C</span>
            {connected ? "1С подключена" : "Подключить 1С"}
            <ChevronDown size={14} />
          </button>
          <span className={`status-dot ${connected ? "online" : ""}`} />
          <button className="icon-button"><Settings size={18} /></button>
        </div>
      </header>

      <main className="workspace-layout">
        <section className="conversation-pane">
          <div className="conversation-scroll">
            <div className="user-bubble">{lastTask}</div>

            <div className="agent-block">
              <div className="agent-avatar"><Bot size={18} /></div>
              <div className="agent-content">
                <div className="run-header">
                  <span>Ход работы · {steps.length} шагов</span>
                  <span className="run-count">{completed}/{steps.length} выполнено</span>
                </div>
                <div className="steps-card">
                  {steps.map((step) => (
                    <div className="step-row" key={step.id}>
                      <span className={`step-icon ${step.status}`}>{statusIcon(step.status)}</span>
                      <div className="step-copy">
                        <span>{step.label}</span>
                        {step.detail && <small>{step.detail}</small>}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="agent-summary">
                  <p>
                    Изменения выполняются только в локальном workspace. При наличии staging-базы harness загружает правку туда,
                    запускает CheckModules и CheckConfig и не касается основной инфобазы без отдельного подтверждения.
                  </p>
                  <div className="summary-chip-row">
                    {snapshots.length > 0 && <span className="summary-chip"><ShieldCheck size={14} /> snapshot создан</span>}
                    {checksOk === true && <span className="summary-chip"><CheckCircle2 size={14} /> staging checks: OK</span>}
                    {doctor?.com_configured && <span className="summary-chip"><Database size={14} /> COM read доступен</span>}
                  </div>
                </div>

                {notice && <div className="notice">{notice}</div>}
              </div>
            </div>
          </div>

          <div className="composer-wrap">
            <div className="context-strip">
              <div className="context-title"><Database size={14} /> Контекст текущей конфигурации</div>
              <span>{doctor?.workspace ?? "workspace / выгруженная конфигурация"}</span>
            </div>
            <div className="composer">
              <textarea
                value={task}
                onChange={(event) => setTask(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitTask();
                  }
                }}
                placeholder="Опишите, что нужно сделать в 1С..."
              />
              <div className="composer-footer">
                <div className="composer-tools">
                  <button className="soft-icon"><Paperclip size={16} /></button>
                  <span className="mode-pill"><MessageSquareText size={13} /> Агент</span>
                </div>
                <button className="send-button" disabled={!task.trim() || running} onClick={() => void submitTask()}>
                  {running ? <RefreshCw size={17} className="spin" /> : <Send size={17} />}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section className="review-pane">
          <div className="review-header">
            <div className="file-tab">
              <FileCode2 size={16} />
              <div>
                <strong>{activePatch.file.split("/").at(-1)}</strong>
                <span>{activePatch.file}</span>
              </div>
            </div>
            <div className="review-meta">
              <span>Review</span>
              <button className="icon-button"><X size={17} /></button>
            </div>
          </div>

          <div className="review-toolbar">
            <span className="change-dot" />
            <span>{activePatch.language.toUpperCase()} · local staged change</span>
            <div className="toolbar-spacer" />
            <button className="toolbar-action"><Play size={14} /> Проверить</button>
            <button className="toolbar-action"><RotateCcw size={14} /> Snapshot</button>
          </div>

          <div className="editor-wrap">
            <DiffEditor
              beforeMount={registerBsl}
              original={activePatch.original}
              modified={activePatch.modified}
              language={activePatch.language}
              theme="vs"
              options={{
                readOnly: true,
                renderSideBySide: false,
                minimap: { enabled: false },
                fontSize: 13,
                lineHeight: 22,
                padding: { top: 18, bottom: 18 },
                scrollBeyondLastLine: false,
                wordWrap: "on",
                renderOverviewRuler: false,
                overviewRulerBorder: false,
                folding: true,
              }}
            />
          </div>

          <div className="review-footer">
            <span className={`review-state ${patchState}`}>
              {patchState === "pending" && "Изменение подготовлено для проверки"}
              {patchState === "accepted" && "Правка принята локально"}
              {patchState === "rejected" && "Правка отклонена и восстановлена"}
            </span>
            <div className="review-buttons">
              <button className="reject-button" onClick={() => void rejectPatch()} disabled={patchState !== "pending"}>Отклонить</button>
              <button className="accept-button" onClick={acceptPatch} disabled={patchState !== "pending"}>
                <Check size={16} /> Принять
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
