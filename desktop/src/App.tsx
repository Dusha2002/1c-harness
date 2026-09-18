import { DiffEditor, type BeforeMount } from '@monaco-editor/react';
import { Bot, Check, Code2, Database, FileCode2, RefreshCw, Send, Settings, Square, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { request, type Progress } from './lib/harness';
import type { DesktopSettings, DiscoveryResult, HarnessDoctor, Session } from './types';
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


const labels: Record<string, string> = {
  read: 'Чтение модуля', search: 'Поиск по исходникам', metadata: 'Изучение метаданных',
  symbols: 'Поиск процедур и функций', patch: 'Изменение кода', diff: 'Просмотр изменений',
  stage_config: 'Загрузка в тестовую базу', check_modules: 'Проверка модулей 1С', check_config: 'Проверка конфигурации',
  run_ui_test: 'Тест интерфейса 1С', runtime_query: 'Запрос к 1С', rollback: 'Восстановление исходников',
};
const settingFields = [
  ['llm_model', 'Модель', 'Например, GigaChat-3-Ultra'],
  ['onec_exe', 'Исполняемый файл 1С', 'C:\\Program Files\\1cv8\\8.3.xx.xxxx\\bin\\1cv8.exe'],
  ['onec_workspace', 'Папка исходников', 'C:\\1C-Harness\\my-project'],
  ['onec_ib_connection', 'Основная база', '/F "C:\\1C\\dev" или /S "server\\base"'],
  ['onec_staging_ib_connection', 'Отдельная тестовая база', '/F "C:\\1C\\staging"'],
  ['onec_user', 'Пользователь 1С', 'Имя пользователя'],
  ['onec_password', 'Пароль 1С', ''],
];

export default function App() {
  const [doctor, setDoctor] = useState<HarnessDoctor | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [active, setActive] = useState(0);
  const [task, setTask] = useState('');
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState('');
  const [events, setEvents] = useState<Progress[]>([]);
  const [currentTool, setCurrentTool] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<DesktopSettings | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
  const [form, setForm] = useState<Record<string, string | null>>({});
  const [check, setCheck] = useState(true);
  const [uiTest, setUiTest] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const pending = session?.review_state === 'pending';
  const file = session?.files[active];

  async function refresh() {
    const status = await request<HarnessDoctor>('doctor');
    setDoctor(status);
    setSession(await request<Session | null>('session'));
    return status;
  }
  useEffect(() => {
    refresh()
      .then(status => { if (!status.can_run) void openSettings(); })
      .catch(error => setNotice(`Не удалось связаться с приложением: ${String(error)}`));
  }, []);

  async function action(work: () => Promise<void>) {
    setBusy(true); setNotice('');
    try { await work(); } catch (error) { setNotice(String(error)); }
    finally { setBusy(false); }
  }

  async function openSettings() {
    await action(async () => {
      const [result, found] = await Promise.all([
        request<DesktopSettings>('settings'),
        request<DiscoveryResult>('discover'),
      ]);
      const next = { ...result.values };
      if (!next.onec_exe && found.executables.length) next.onec_exe = found.executables[0];
      if ((!next.onec_workspace || next.onec_workspace === 'workspace') && found.suggested_workspace) {
        next.onec_workspace = found.suggested_workspace;
      }
      if (!next.onec_ib_connection && found.infobases.length === 1) {
        next.onec_ib_connection = found.infobases[0].connection;
      }
      setSettings(result); setDiscovery(found); setForm(next); setSettingsOpen(true);
    });
  }

  async function saveSettings() {
    await action(async () => {
      const result = await request<DesktopSettings>('save_settings', { values: form });
      setSettings(result); setForm(result.values);
      await refresh();
      setNotice('Настройки сохранены. Можно проверить модель и выгрузить исходники.');
    });
  }

  async function run() {
    if (!task.trim() || busy || pending) return;
    const prompt = task.trim();
    setRunning(true); setEvents([]); setCurrentTool('Подключение к модели');
    await action(async () => {
      try {
        const result = await request<Session>('run', { task: prompt, check, ui_test: uiTest }, event => {
          if (event.type === 'thinking') setCurrentTool(`Модель обдумывает шаг ${event.iteration}`);
          if (event.type === 'tool_start') setCurrentTool(labels[event.tool ?? ''] ?? event.tool ?? '');
          if (event.type === 'tool_end') setEvents(items => [...items, event]);
        });
        setSession(result); setActive(0); setTask('');
      } catch (error) {
        // Recover the durable session even if the desktop transport failed.
        await refresh();
        throw error;
      }
    });
    setCurrentTool(''); setRunning(false);
  }

  const steps = running ? events : session?.steps ?? [];
  const changeField = (key: string, value: string) => setForm(current => ({ ...current, [key]: value || null }));
  const secretKey = form.llm_provider === 'gigachat' ? 'gigachat_credentials'
    : form.llm_provider === 'anthropic' ? 'anthropic_api_key' : 'llm_api_key';

  function field(key: string, title: string, placeholder: string) {
    const secret = key.includes('password') || key.includes('api_key') || key === 'gigachat_credentials' || key === 'onec_com_connection';
    return <label className="settings-field" key={key}><span>{title}</span>
      <input type={secret ? 'password' : 'text'} value={form[key] ?? ''} autoComplete="off"
        placeholder={secret && settings?.secrets[key] ? 'Сохранён — оставьте пустым, чтобы не менять' : placeholder}
        onChange={event => secret
          ? setForm(current => ({ ...current, [key]: event.target.value }))
          : changeField(key, event.target.value)} />
      {secret && settings?.secrets[key] && <button className="clear-secret" onClick={() => setForm(current => ({ ...current, [key]: null }))}>Удалить сохранённый ключ/пароль при сохранении</button>}
    </label>;
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-block"><div className="brand-mark"><Code2 size={17}/></div><span className="brand-name">1C Harness</span></div>
      <div className="session-title">{doctor?.llm_model ?? 'Новая сессия'}</div>
      <div className="top-actions">
        <button className="connect-button" disabled={busy || pending} onClick={() => void openSettings()}><span className="onec-badge">1C</span>Подключение и модель</button>
        <button className="icon-button" aria-label="Обновить состояние" disabled={busy} onClick={() => void action(refresh)}><RefreshCw size={17}/></button>
        <button className="icon-button" aria-label="Настройки" disabled={busy || pending} onClick={() => void openSettings()}><Settings size={18}/></button>
      </div>
    </header>
    <main className="workspace-layout">
      <section className="conversation-pane">
        <div className="conversation-scroll">
          {(running || session) && <div className="user-bubble">{running ? task : session?.task}</div>}
          <div className="agent-block">
            <div className="agent-avatar"><Bot size={18}/></div>
            <div className="agent-content">
              {!session && !running && <div className="welcome"><h2>Разрабатывайте на 1С<br/>вместе с ИИ</h2><p>Подключите модель, выберите исходники конфигурации и опишите задачу. Все изменения появятся справа для проверки.</p><button className="accept-button" disabled={busy} onClick={() => void openSettings()}>Настроить подключение</button></div>}
              {steps.length > 0 && <div className="run-header">Ход работы · {steps.length} шагов</div>}
              <div className="steps-card">
                {steps.map((step, i) => {
                  const failed = /ERROR:|: FAILED|"success": false/.test(step.result ?? '');
                  return <details className="step-details" key={i}><summary><span className={`step-icon ${failed ? 'failed' : 'done'}`}>{failed ? <X size={12}/> : <Check size={12}/>}</span>{labels[step.tool ?? ''] ?? step.tool}</summary><pre>{step.result}</pre></details>;
                })}
                {running && <div className="live-step"><RefreshCw size={14} className="spin"/>{currentTool}</div>}
              </div>
              {session && !running && <div className="agent-summary"><p className="preserve-lines">{session.summary}</p><div className="summary-chip-row">
                <span className="summary-chip">{session.checks_ok === true ? 'Проверки 1С пройдены' : session.checks_ok === false ? 'Проверки 1С не пройдены' : 'Проверки 1С не выполнялись'}</span>
                {session.ui_test_ok !== null && <span className="summary-chip">E2E: {session.ui_test_ok ? 'пройден' : 'ошибка'}</span>}
                {session.status !== 'completed' && <span className="summary-chip">Запуск не завершён: {session.status}</span>}
              </div></div>}
              {notice && <div className="notice" role="status">{notice}</div>}
            </div>
          </div>
        </div>
        <div className="composer-wrap">
          <div className="context-strip"><div className="context-title"><Database size={14}/>Исходники конфигурации</div><span>{doctor?.source_count ?? 0} файлов</span></div>
          <div className="workspace-path" title={doctor?.workspace}>{doctor?.workspace ?? 'Выберите папку в настройках'}</div>
          <div className="composer">
            <textarea aria-label="Задача для ИИ" value={task} disabled={running} onChange={event => setTask(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void run(); } }}
              placeholder={pending ? 'Сначала примите или отклоните изменения' : 'Опишите, что нужно сделать в 1С…'}/>
            <div className="composer-footer"><div className="composer-tools">
              <label className="mode-pill"><input type="checkbox" checked={check} disabled={busy} onChange={event => { setCheck(event.target.checked); if (!event.target.checked) setUiTest(false); }}/>Проверять в 1С</label>
              <label className="mode-pill"><input type="checkbox" checked={uiTest} disabled={busy || !check || !doctor?.e2e_ui_testing} onChange={event => setUiTest(event.target.checked)}/>UI-тест</label>
            </div>
            {running ? <button className="send-button" aria-label="Остановить" onClick={() => void request('cancel').then(() => setNotice('Остановка после текущего запроса модели или команды 1С.')).catch(error => setNotice(String(error)))}><Square size={14}/></button>
              : <button className="send-button" aria-label="Отправить" disabled={!task.trim() || busy || pending || !doctor?.can_run} onClick={() => void run()}><Send size={17}/></button>}
            </div>
          </div>
        </div>
      </section>
      <section className="review-pane">
        <div className="review-header"><div className="file-tab"><FileCode2 size={16}/><strong>Изменения</strong></div>
          {session && session.files.length > 0 && <select aria-label="Изменённый файл" value={active} onChange={event => setActive(Number(event.target.value))}>{session.files.map((item, index) => <option key={item.path} value={index}>{item.path}</option>)}</select>}
        </div>
        <div className="review-toolbar">{session?.review_state === 'accepted' && session.files.length > 0 && <button className="toolbar-action" disabled={busy || session.checks_ok !== true || session.deployment === 'applied'} onClick={() => setApplyOpen(true)}>{session.deployment === 'applied' ? 'Применено в 1С' : 'Применить в 1С…'}</button>}<span>{file ? `${active + 1} / ${session?.files.length} · ${file.created ? 'Новый файл' : file.deleted ? 'Удаление' : 'Изменение'}` : 'Здесь появится сравнение кода'}</span></div>
        <div className="editor-wrap">{file ? <DiffEditor beforeMount={registerBsl} original={file.original} modified={file.modified}
          language={file.path.endsWith('.bsl') ? 'bsl' : 'xml'} theme="vs" options={{readOnly: true, renderSideBySide: false, minimap: {enabled: false}, fontSize: 13, lineHeight: 22, scrollBeyondLastLine: false, wordWrap: 'on'}}/>
          : <div className="empty-review"><Code2 size={38} strokeWidth={1}/><h3>Каждое изменение — на виду</h3><p>Полный код до и после.<br/>Принятие или отклонение всей задачи.</p></div>}</div>
        <div className="review-footer"><span className={`review-state ${session?.review_state}`}>{pending ? 'Ожидает вашего решения' : session?.review_state === 'accepted' ? 'Сохранено в исходниках' : session?.review_state === 'rejected' ? 'Исходники восстановлены' : 'Нет изменений'}</span>
          <div className="review-buttons"><button className="reject-button" disabled={!pending || busy} onClick={() => void action(async () => setSession(await request<Session>('reject')))}>Отклонить всё</button><button className="accept-button" disabled={!pending || busy || session?.status !== 'completed' || session?.checks_ok === false} onClick={() => void action(async () => setSession(await request<Session>('accept')))}><Check size={15}/>Принять всё</button></div>
        </div>
      </section>
    </main>
    {applyOpen && <div className="modal-backdrop"><section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="apply-title"><h2 id="apply-title">Применить изменения в основной базе?</h2><p className="settings-intro">Приложение создаст резервную копию .dt, загрузит проверенные исходники и обновит конфигурацию базы данных. Завершите другие сеансы работы с этой базой. При ошибке путь к резервной копии появится в сообщении.</p><div className="settings-actions"><button className="reject-button" disabled={busy} onClick={() => setApplyOpen(false)}>Отмена</button><button className="accept-button" disabled={busy} onClick={() => { setApplyOpen(false); void action(async () => { const result = await request<Session>('apply', {confirmed: true}); setSession(result); setNotice(`Применено. Резервная копия: ${result.backup}`); }); }}>Создать копию и применить</button></div></section></div>}
    {settingsOpen && <div className="modal-backdrop"><section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div className="settings-heading"><h2 id="settings-title">Подключение</h2><button className="icon-button" aria-label="Закрыть настройки" disabled={busy} onClick={() => setSettingsOpen(false)}><X size={20}/></button></div>
      <p className="settings-intro">Настройте модель и 1С здесь. Для проверок используйте отдельную копию базы. Принятие правок сохраняет исходники; основная база автоматически не обновляется.</p>
      {discovery && <div className="discovery-panel">
        <div className="discovery-title">Автообнаружение 1С</div>
        <div className="settings-grid compact-grid">
          <label className="settings-field"><span>Установленная платформа</span>
            <select value={form.onec_exe ?? ''} onChange={event => changeField('onec_exe', event.target.value)}>
              <option value="">Не выбрано</option>
              {discovery.executables.map(exe => <option key={exe} value={exe}>{exe}</option>)}
            </select>
          </label>
          <label className="settings-field"><span>Основная база</span>
            <select value={form.onec_ib_connection ?? ''} onChange={event => changeField('onec_ib_connection', event.target.value)}>
              <option value="">Не выбрано</option>
              {discovery.infobases.map(base => <option key={`primary-${base.connection}`} value={base.connection}>{base.name}</option>)}
            </select>
          </label>
          <label className="settings-field"><span>Staging-база</span>
            <select value={form.onec_staging_ib_connection ?? ''} onChange={event => changeField('onec_staging_ib_connection', event.target.value)}>
              <option value="">Выберите отдельную копию</option>
              {discovery.infobases.filter(base => base.connection !== form.onec_ib_connection)
                .map(base => <option key={`staging-${base.connection}`} value={base.connection}>{base.name}</option>)}
            </select>
          </label>
          <label className="settings-field"><span>Workspace</span>
            <div className="suggested-path">{form.onec_workspace ?? discovery.suggested_workspace}</div>
          </label>
        </div>
        {discovery.executables.length === 0 && <small>Платформа не найдена автоматически — путь можно указать ниже вручную.</small>}
        {discovery.infobases.length === 0 && <small>Зарегистрированные базы не найдены — подключение можно указать ниже вручную.</small>}
      </div>}
      <div className="settings-grid"><label className="settings-field"><span>Провайдер ИИ</span><select value={form.llm_provider ?? 'gigachat'} onChange={event => changeField('llm_provider', event.target.value)}>{['gigachat','openai','deepseek','anthropic','openai_compatible'].map(provider => <option key={provider} value={provider}>{provider}</option>)}</select></label>
        {field(secretKey, form.llm_provider === 'gigachat' ? 'Authorization Key GigaChat' : 'API-ключ', '')}
        {settingFields.map(([key, title, placeholder]) => field(key, title, placeholder))}
        {form.llm_provider === 'openai_compatible' && field('openai_compatible_base_url', 'API Base URL', 'https://provider.example/v1')}
      </div>
      <details className="advanced-settings"><summary>Дополнительно: COM, сертификат, UI-тесты</summary><div className="settings-grid">
        {field('gigachat_ca_bundle', 'CA-сертификат GigaChat (PEM)', 'Путь к файлу сертификата')}
        {field('onec_com_connection', 'COM connection string', 'File="C:\\1C\\dev";')}
        {field('onec_test_manager_connection', 'База Test Manager', '/F "C:\\1C\\test-manager"')}
        {field('onec_test_manager_user', 'Пользователь Test Manager', '')}
        {field('onec_test_manager_password', 'Пароль Test Manager', '')}
      </div></details>
      {doctor && <div className="connection-report">Исходники: {doctor.source_count} · Путь к 1С: {doctor.exe_exists ? 'найден' : 'не найден'} · Ключ модели: {doctor.llm_credentials ? 'сохранён' : 'не задан'}{doctor.errors.map(error => <p key={error}>{error}</p>)}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      <div className="settings-actions"><button className="reject-button" disabled={busy} onClick={() => void action(async () => { const data = await request<{message: string}>('test_model'); setNotice(`Модель ответила: ${data.message}`); })}>Проверить модель</button>
        <button className="reject-button" disabled={busy} onClick={() => void action(async () => { setDoctor(await request<HarnessDoctor>('export')); setNotice('Исходники выгружены из 1С. Можно начать задачу.'); })}>Выгрузить из 1С</button>
        <button className="accept-button" disabled={busy} onClick={() => void saveSettings()}>{busy ? 'Подождите…' : 'Сохранить настройки'}</button></div>
      <small className="settings-note">Проверка модели и выгрузка используют сохранённые настройки. Ключи на Windows защищены шифрованием учётной записи.</small>
    </section></div>}
  </div>;
}
