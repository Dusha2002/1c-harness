import { Bot, Check, ChevronLeft, ChevronRight, Database, FolderOpen, LoaderCircle, MonitorCog, Search, ShieldCheck, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { pickPath, request } from './lib/harness';
import type { DesktopSettings, DiscoveryResult, DiscoveredInfobase, HarnessDoctor } from './types';

interface Props {
  onComplete: (doctor: HarnessDoctor) => void;
  onAdvanced: () => void;
}

const providerLabels: Record<string, string> = {
  gigachat: 'GigaChat',
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  anthropic: 'Anthropic',
  openai_compatible: 'Другой API',
};

const providerModels: Record<string, string> = {
  gigachat: 'GigaChat-3-Ultra',
  openai: '',
  deepseek: '',
  anthropic: '',
  openai_compatible: '',
};

function fileConnection(path: string) {
  return `/F "${path}"`;
}

function stagingTarget(path: string) {
  return path.replace(/[\\/]+$/, '') + '-harness-staging';
}

export default function SetupWizard({ onComplete, onAdvanced }: Props) {
  const [step, setStep] = useState(0);
  const [settings, setSettings] = useState<DesktopSettings | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryResult>({ executables: [], infobases: [], suggested_workspace: '' });
  const [form, setForm] = useState<Record<string, string | null>>({});
  const [selectedBase, setSelectedBase] = useState<DiscoveredInfobase | null>(null);
  const [autoStaging, setAutoStaging] = useState(true);
  const [busy, setBusy] = useState(true);
  const [scanBusy, setScanBusy] = useState<'platforms' | 'bases' | null>(null);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    Promise.all([
      request<DesktopSettings>('settings'),
      request<{suggested_workspace: string}>('discovery_defaults'),
    ]).then(([saved, defaults]) => {
      if (!active) return;
      const next = { ...saved.values };
      if ((!next.onec_workspace || next.onec_workspace === 'workspace') && defaults.suggested_workspace) {
        next.onec_workspace = defaults.suggested_workspace;
      }
      setSettings(saved);
      setDiscovery(current => ({ ...current, suggested_workspace: defaults.suggested_workspace }));
      setForm(next);
    }).catch(reason => active && setError(String(reason)))
      .finally(() => active && setBusy(false));
    return () => { active = false; };
  }, []);

  async function scanPlatforms() {
    setScanBusy('platforms'); setError('');
    try {
      const found = await request<{executables: string[]; suggested_workspace: string}>('discover_platforms');
      setDiscovery(current => ({ ...current, executables: found.executables, suggested_workspace: found.suggested_workspace }));
      if (found.executables.length > 0 && !form.onec_exe) setValue('onec_exe', found.executables[0]);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setScanBusy(null);
    }
  }

  async function scanBases() {
    setScanBusy('bases'); setError('');
    try {
      const found = await request<{infobases: DiscoveredInfobase[]; suggested_workspace: string}>('discover_bases');
      setDiscovery(current => ({ ...current, infobases: found.infobases, suggested_workspace: found.suggested_workspace }));
      if (found.infobases.length === 1 && !form.onec_ib_connection) chooseBase(found.infobases[0]);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setScanBusy(null);
    }
  }

  const secretKey = form.llm_provider === 'gigachat' ? 'gigachat_credentials'
    : form.llm_provider === 'anthropic' ? 'anthropic_api_key' : 'llm_api_key';

  const hasSecret = Boolean(form[secretKey]) || Boolean(settings?.secrets[secretKey]);
  const canContinue = useMemo(() => {
    if (step === 0) return Boolean(form.onec_exe);
    if (step === 1) return Boolean(form.onec_ib_connection) && (autoStaging ? Boolean(selectedBase?.file_path) : Boolean(form.onec_staging_ib_connection));
    if (step === 2) return Boolean(form.llm_provider && form.llm_model && hasSecret);
    return Boolean(form.onec_workspace);
  }, [step, form, autoStaging, selectedBase, hasSecret]);

  function setValue(key: string, value: string | null) {
    setForm(current => ({ ...current, [key]: value || null }));
  }

  function chooseBase(base: DiscoveredInfobase) {
    setSelectedBase(base);
    setValue('onec_ib_connection', base.connection);
    if (base.file_path && !form.onec_staging_ib_connection) setAutoStaging(true);
  }

  async function browseExe() {
    const path = await pickPath('exe');
    if (path) setValue('onec_exe', path);
  }

  async function browseBase() {
    const path = await pickPath('folder');
    if (!path) return;
    const base = { name: path.split(/[\\/]/).filter(Boolean).at(-1) ?? 'Файловая база', connection: fileConnection(path), file_path: path };
    setSelectedBase(base);
    chooseBase(base);
  }

  async function browseWorkspace() {
    const path = await pickPath('folder');
    if (path) setValue('onec_workspace', path);
  }

  async function finish() {
    if (!canContinue || busy) return;
    setBusy(true); setError('');
    try {
      const next = { ...form };
      setProgress('Сохраняю подключение…');
      await request('save_settings', { values: next });

      if (autoStaging && selectedBase?.file_path) {
        const target = stagingTarget(selectedBase.file_path);
        setProgress('Создаю безопасную staging-копию базы…');
        const created = await request<{connection: string; path: string}>('prepare_staging', { target });
        next.onec_staging_ib_connection = created.connection;
        await request('save_settings', { values: { onec_staging_ib_connection: created.connection } });
      }

      setProgress('Проверяю AI-модель…');
      await request('test_model');

      setProgress('Выгружаю конфигурацию 1С…');
      const doctor = await request<HarnessDoctor>('export');
      setProgress('Готово');
      onComplete(doctor);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) {
    return <div className="setup-backdrop"><div className="setup-loading"><LoaderCircle className="spin" size={20}/> Загружаю настройки…</div></div>;
  }

  const steps = [
    ['Платформа 1С', MonitorCog],
    ['Рабочая база', Database],
    ['AI-модель', Sparkles],
    ['Готовность', ShieldCheck],
  ] as const;

  return <div className="setup-backdrop">
    <section className="setup-window" role="dialog" aria-modal="true" aria-label="Первичная настройка 1C Harness">
      <header className="setup-header">
        <div className="setup-brand"><div className="setup-logo"><Bot size={20}/></div><div><strong>Настройка 1C Harness</strong><span>Один раз — дальше всё работает из приложения</span></div></div>
        <button className="setup-link" onClick={onAdvanced} disabled={busy}>Расширенные настройки</button>
      </header>

      <div className="setup-progress">
        {steps.map(([title, Icon], index) => <button key={title} className={`setup-step ${step === index ? 'active' : ''} ${index < step ? 'done' : ''}`}
          onClick={() => index < step && setStep(index)} disabled={busy}>
          <span>{index < step ? <Check size={13}/> : <Icon size={14}/>}</span>{title}
        </button>)}
      </div>

      <div className="setup-body">
        {step === 0 && <div className="setup-section">
          <div className="setup-copy"><h2>Выбери платформу 1С</h2>
            <p>Поиск по компьютеру не запускается автоматически. Можно найти установленную платформу кнопкой или выбрать 1cv8.exe вручную.</p></div>
          <button className="setup-scan-button" disabled={scanBusy !== null} onClick={() => void scanPlatforms()}>
            {scanBusy === 'platforms' ? <LoaderCircle className="spin" size={15}/> : <Search size={15}/>}
            {scanBusy === 'platforms' ? 'Ищу установленную 1С…' : 'Найти установленную 1С'}
          </button>
          <div className="setup-choice-list">
            {discovery.executables.map((exe, i) => <button key={exe} className={`setup-choice ${form.onec_exe === exe ? 'selected' : ''}`} onClick={() => setValue('onec_exe', exe)}>
              <span className="onec-badge">1C</span><div><strong>1С:Предприятие {i === 0 ? '· рекомендуется' : ''}</strong><small>{exe}</small></div>{form.onec_exe === exe && <Check size={16}/>}
            </button>)}
          </div>
          <div className="setup-path-row"><label><span>Путь к 1cv8.exe</span><input value={form.onec_exe ?? ''} onChange={e => setValue('onec_exe', e.target.value)}/></label>
            <button className="setup-browse" onClick={() => void browseExe()}><FolderOpen size={15}/>Выбрать</button></div>
        </div>}

        {step === 1 && <div className="setup-section">
          <div className="setup-copy"><h2>Выбери рабочую базу</h2><p>Основная база не используется для автономных проверок. Для них нужна отдельная staging-копия.</p></div>
          <button className="setup-scan-button" disabled={scanBusy !== null} onClick={() => void scanBases()}>
            {scanBusy === 'bases' ? <LoaderCircle className="spin" size={15}/> : <Database size={15}/>}
            {scanBusy === 'bases' ? 'Ищу зарегистрированные базы…' : 'Найти зарегистрированные базы'}
          </button>
          <div className="setup-choice-list base-list">
            {discovery.infobases.map(base => <button key={base.connection} className={`setup-choice ${form.onec_ib_connection === base.connection ? 'selected' : ''}`} onClick={() => chooseBase(base)}>
              <Database size={18}/><div><strong>{base.name}</strong><small>{base.connection}</small></div>{form.onec_ib_connection === base.connection && <Check size={16}/>}
            </button>)}
          </div>
          <div className="setup-path-row"><label><span>Основная база</span><input value={form.onec_ib_connection ?? ''} onChange={e => { setSelectedBase(null); setValue('onec_ib_connection', e.target.value); }}/></label>
            <button className="setup-browse" onClick={() => void browseBase()}><FolderOpen size={15}/>Папка базы</button></div>

          {selectedBase?.file_path && <div className="setup-staging-card"><div><strong><ShieldCheck size={16}/> Создать staging автоматически</strong>
            <p>Закрой сеансы этой файловой базы. Harness скопирует её в <b>{stagingTarget(selectedBase.file_path)}</b>.</p></div>
            <input type="checkbox" checked={autoStaging} onChange={e => setAutoStaging(e.target.checked)}/></div>}

          {!autoStaging && <label className="setup-field"><span>Отдельная staging-база</span>
            <select value={form.onec_staging_ib_connection ?? ''} onChange={e => setValue('onec_staging_ib_connection', e.target.value)}>
              <option value="">Не выбрано</option>
              {discovery.infobases.filter(base => base.connection !== form.onec_ib_connection).map(base => <option key={base.connection} value={base.connection}>{base.name}</option>)}
            </select>
            <input placeholder='/F "C:\\1C\\staging" или /S "server\\staging"' value={form.onec_staging_ib_connection ?? ''} onChange={e => setValue('onec_staging_ib_connection', e.target.value)}/>
          </label>}
          {!selectedBase?.file_path && autoStaging && <div className="setup-warning">Для серверной или вручную указанной базы автоматическое копирование недоступно. Выбери существующую staging-базу.</div>}
          {!selectedBase?.file_path && <button className="setup-secondary-inline" onClick={() => setAutoStaging(false)}>Выбрать staging вручную</button>}
        </div>}

        {step === 2 && <div className="setup-section">
          <div className="setup-copy"><h2>Подключи AI</h2><p>Провайдера и модель можно поменять позже. Секреты на Windows сохраняются через DPAPI.</p></div>
          <div className="setup-provider-grid">
            {Object.entries(providerLabels).map(([id, label]) => <button key={id} className={`setup-provider ${form.llm_provider === id ? 'selected' : ''}`}
              onClick={() => setForm(current => ({...current, llm_provider: id, llm_model: current.llm_provider === id ? current.llm_model : providerModels[id]}))}>
              <Sparkles size={16}/>{label}{form.llm_provider === id && <Check size={14}/>}
            </button>)}
          </div>
          <label className="setup-field"><span>Модель</span><input value={form.llm_model ?? ''} onChange={e => setValue('llm_model', e.target.value)}/></label>
          <label className="setup-field"><span>{form.llm_provider === 'gigachat' ? 'Authorization Key GigaChat' : 'API-ключ'}</span>
            <input type="password" autoComplete="off" placeholder={settings.secrets[secretKey] ? 'Ключ уже сохранён' : 'Вставь ключ'} value={form[secretKey] ?? ''} onChange={e => setValue(secretKey, e.target.value)}/>
          </label>
          {form.llm_provider === 'openai_compatible' && <label className="setup-field"><span>API Base URL</span><input value={form.openai_compatible_base_url ?? ''} onChange={e => setValue('openai_compatible_base_url', e.target.value)}/></label>}
        </div>}

        {step === 3 && <div className="setup-section">
          <div className="setup-copy"><h2>Остался один клик</h2><p>Harness сохранит настройки, подготовит staging, проверит модель и выгрузит исходники конфигурации.</p></div>
          <div className="setup-ready">
            <div><Check size={14}/><span>Платформа</span><strong>{form.onec_exe}</strong></div>
            <div><Check size={14}/><span>Основная база</span><strong>{selectedBase?.name ?? form.onec_ib_connection}</strong></div>
            <div><Check size={14}/><span>Staging</span><strong>{autoStaging ? 'будет создана автоматически' : form.onec_staging_ib_connection}</strong></div>
            <div><Check size={14}/><span>AI</span><strong>{providerLabels[form.llm_provider ?? ''] ?? form.llm_provider} · {form.llm_model}</strong></div>
          </div>
          <div className="setup-path-row"><label><span>Рабочее пространство</span><input value={form.onec_workspace ?? ''} onChange={e => setValue('onec_workspace', e.target.value)}/></label>
            <button className="setup-browse" onClick={() => void browseWorkspace()}><FolderOpen size={15}/>Выбрать</button></div>
          {progress && <div className="setup-running"><RefreshIcon/> {progress}</div>}
        </div>}
        {error && <div className="setup-error">{error}</div>}
      </div>

      <footer className="setup-footer">
        <button className="setup-secondary" disabled={step === 0 || busy} onClick={() => setStep(s => Math.max(0, s - 1))}><ChevronLeft size={15}/>Назад</button>
        {step < 3
          ? <button className="setup-primary" disabled={!canContinue || busy} onClick={() => setStep(s => s + 1)}>Продолжить<ChevronRight size={15}/></button>
          : <button className="setup-primary" disabled={!canContinue || busy} onClick={() => void finish()}>{busy ? <LoaderCircle className="spin" size={16}/> : <Sparkles size={16}/>}Настроить и начать</button>}
      </footer>
    </section>
  </div>;
}

function RefreshIcon() {
  return <LoaderCircle size={15} className="spin"/>;
}
