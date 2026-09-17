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
import type { AgentStep, HarnessDoctor, StepStatus } from "./types";

const registerBsl: BeforeMount = (monaco) => {
  if (monaco.languages.getLanguages().some((language) => language.id === "bsl")) return;

  monaco.languages.register({ id: "bsl" });
  monaco.languages.setMonarchTokensProvider("bsl", {
    ignoreCase: true,
    keywords: [
      "Процедура",
      "КонецПроцедуры",
      "Функция",
      "КонецФункции",
      "Если",
      "Тогда",
      "Иначе",
      "КонецЕсли",
      "Для",
      "Каждого",
      "Из",
      "Цикл",
      "КонецЦикла",
      "Возврат",
      "Истина",
      "Ложь",
      "Неопределено",
      "Новый",
      "Экспорт",
      "Перем",
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
    patch: "Подготовлена правка",
    diff: "Diff проверен агентом",
    check_modules: "CheckModules выполнен",
    check_config: "CheckConfig выполнен",
    rollback: "Правка откатана",
  };
  return labels[tool] ?? tool;
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [doctor, setDoctor] = useState<HarnessDoctor | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>(demoSteps);
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [patchState, setPatchState] = useState<"pending" | "accepted" | "rejected">("pending");
  const [notice, setNotice] = useState<string | null>(null);

  const completed = useMemo(() => steps.filter((step) => step.status === "done").length, [steps]);

  async function connectHarness() {
    setNotice(null);
    try {
      const { getDoctor } = await import("./lib/harness");
      const result = await getDoctor();
      setDoctor(result);
      setConnected(Boolean(result.onec_exe && result.onec_connection));
      if (!result.onec_exe || !result.onec_connection) {
        setNotice("Harness найден, но путь к 1С или подключение к инфобазе ещё не настроены.");
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
    setSteps([{ id: "live-1", label: "Агент анализирует задачу", status: "running" }]);

    try {
      const { runAgent } = await import("./lib/harness");
      const result = await runAgent(trimmed, { write: false, check: false });
      setSteps(
        result.steps.map((step, index) => ({
          id: `live-${index}`,
          label: toolLabel(step.tool),
          detail: step.result.split("\n")[0].slice(0, 120),
          status: "done" as const,
        })),
      );
      setNotice(result.summary);
      setTask("");
    } catch (error) {
      setSteps([{ id: "error", label: "Не удалось запустить локальный harness", detail: String(error), status: "failed" }]);
    } finally {
      setRunning(false);
    }
  }

  function acceptPatch() {
    setPatchState("accepted");
    setNotice("Правка принята в локальном workspace. Загрузка в 1С остаётся отдельным подтверждаемым действием.");
  }

  function rejectPatch() {
    setPatchState("rejected");
    setNotice("Правка отклонена. Для реальной сессии harness восстановит snapshot затронутого файла.");
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
          <span className="session-time">GigaChat 3 Ultra</span>
        </div>

        <div className="top-actions">
          <button className={`connect-button ${connected ? "connected" : ""}`} onClick={connectHarness}>
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
            <div className="user-bubble">
              Проверь проведение документа ЗаказПокупателя и не позволяй проводить его при недостаточном остатке товара.
            </div>

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
                    Найдена логика проведения документа и связанный регистр остатков. Harness сделал snapshot,
                    подготовил минимальную BSL-правку и показал её справа до загрузки в 1С.
                  </p>
                  <div className="summary-chip-row">
                    <span className="summary-chip"><ShieldCheck size={14} /> snapshot создан</span>
                    <span className="summary-chip"><CheckCircle2 size={14} /> CheckModules: OK</span>
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
                <strong>{demoPatch.file.split("/").at(-1)}</strong>
                <span>{demoPatch.file}</span>
              </div>
            </div>
            <div className="review-meta">
              <span>Правка 1 / 1</span>
              <button className="icon-button"><X size={17} /></button>
            </div>
          </div>

          <div className="review-toolbar">
            <span className="change-dot" />
            <span>BSL · staged change</span>
            <div className="toolbar-spacer" />
            <button className="toolbar-action"><Play size={14} /> Проверить</button>
            <button className="toolbar-action"><RotateCcw size={14} /> Snapshot</button>
          </div>

          <div className="editor-wrap">
            <DiffEditor
              beforeMount={registerBsl}
              original={demoPatch.original}
              modified={demoPatch.modified}
              language="bsl"
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
            <span className={`review-state ${patchState}` }>
              {patchState === "pending" && "Изменение подготовлено для проверки"}
              {patchState === "accepted" && "Правка принята локально"}
              {patchState === "rejected" && "Правка отклонена"}
            </span>
            <div className="review-buttons">
              <button className="reject-button" onClick={rejectPatch} disabled={patchState !== "pending"}>Отклонить</button>
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
