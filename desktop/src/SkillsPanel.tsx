import { BookOpen, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { pickPath, request } from './lib/harness';
import type { SkillInfo } from './types';

export default function SkillsPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  async function refresh() {
    setSkills(await request<SkillInfo[]>('skills'));
  }

  useEffect(() => {
    void refresh().catch(error => setNotice(String(error)));
  }, []);

  async function importSkill() {
    const path = await pickPath('skill');
    if (!path) return;
    setBusy(true); setNotice('');
    try {
      const added = await request<SkillInfo>('import_skill', { path });
      await refresh();
      setNotice(`Добавлен skill://${added.name}`);
    } catch (error) {
      setNotice(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function removeSkill(name: string) {
    setBusy(true); setNotice('');
    try {
      setSkills(await request<SkillInfo[]>('delete_skill', { name }));
      setNotice(`Удалён skill://${name}`);
    } catch (error) {
      setNotice(String(error));
    } finally {
      setBusy(false);
    }
  }

  return <section className="skills-panel">
    <div className="skills-heading">
      <div><BookOpen size={16}/><div><strong>Skills</strong><span>Полный текст подгружается моделью только когда нужен.</span></div></div>
      <button className="skill-add" disabled={busy} onClick={() => void importSkill()}><Plus size={14}/>Добавить .md/.txt</button>
    </div>
    <div className="skill-list">
      {skills.map(skill => <div className="skill-row" key={skill.name}>
        <div className="skill-icon"><BookOpen size={14}/></div>
        <div className="skill-copy"><strong>skill://{skill.name}</strong><span>{skill.description}</span></div>
        <span className={`skill-source ${skill.source}`}>{skill.source === 'builtin' ? 'встроенный' : 'мой'}</span>
        {skill.source === 'user' && <button className="skill-delete" aria-label={`Удалить ${skill.name}`} disabled={busy}
          onClick={() => void removeSkill(skill.name)}><Trash2 size={14}/></button>}
      </div>)}
    </div>
    <div className="skills-note">Формат: Markdown/TXT. Опционально в начале: <code>name:</code> и <code>description:</code> во frontmatter. Skills не могут расширять права агента или обходить staging/safety.</div>
    {notice && <div className="skill-notice">{notice}</div>}
  </section>;
}
