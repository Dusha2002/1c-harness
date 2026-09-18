"""One JSON request on stdin, NDJSON progress and final response on stdout."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from onec_harness.agent import HarnessAgent
from onec_harness.connections import connection_identity, require_test_connection
from onec_harness.desktop_config import config_root, load_settings, public_config, write_config
from onec_harness.onec.com import ComConnector
from onec_harness.onec.designer import Designer
from onec_harness.onec.e2e import TestManagerRunner
from onec_harness.onec.testing import ScenarioCompiler
from onec_harness.providers.base import Message
from onec_harness.providers.factory import create_provider
from onec_harness.workspace import Workspace


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def source_bytes(workspace: Workspace) -> dict[str, str]:
    return {name: base64.b64encode(workspace.resolve(name).read_bytes()).decode('ascii')
            for name in workspace.source_texts()}


def decode(value: str | None) -> str:
    return base64.b64decode(value).decode('utf-8-sig') if value is not None else ''


class DesktopService:
    def __init__(self, emit_event=emit):
        self.settings = load_settings()
        self.workspace = Workspace(self.settings.onec_workspace)
        self.path = self.workspace.root / '.onec-harness' / 'desktop-session.json'
        self.emit = emit_event

    def read_session(self) -> dict | None:
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None

    def review(self, session: dict | None = None) -> dict | None:
        session = session or self.read_session()
        if not session:
            return None
        before = session['before']
        # An interrupted process has a durable baseline and can still be reviewed.
        after = session.get('after', source_bytes(self.workspace))
        files = [{'path': p, 'original': decode(before.get(p)), 'modified': decode(after.get(p)),
                  'created': p not in before, 'deleted': p not in after}
                 for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
        return {k: v for k, v in session.items() if k not in {'before', 'after'}} | {'files': files}

    def doctor(self) -> dict:
        s = self.settings
        credentials = bool(s.gigachat_credentials if s.provider_name == 'gigachat' else
                           s.anthropic_api_key if s.provider_name == 'anthropic' else s.llm_api_key)
        errors = []
        for connection in (s.onec_ib_connection, s.onec_staging_ib_connection, s.onec_test_manager_connection):
            if connection:
                try:
                    connection_identity(connection)
                except ValueError as exc:
                    errors.append(str(exc))
        if s.onec_staging_ib_connection:
            try:
                require_test_connection(s.onec_ib_connection, s.onec_staging_ib_connection)
            except ValueError as exc:
                errors.append(str(exc))
        sources = self.workspace.source_texts() if self.workspace.root.exists() else {}
        exe_ok = bool(s.onec_exe and s.onec_exe.is_file())
        return {'llm_provider': s.llm_provider, 'llm_model': s.llm_model, 'llm_credentials': credentials,
                'onec_exe': str(s.onec_exe) if s.onec_exe else None, 'exe_exists': exe_ok,
                'onec_connection': bool(s.onec_ib_connection), 'staging_connection': bool(s.onec_staging_ib_connection),
                'workspace': str(self.workspace.root), 'source_count': len(sources), 'errors': errors,
                'can_run': bool(credentials and sources and not errors),
                'can_check': bool(exe_ok and s.onec_staging_ib_connection and not errors),
                'e2e_ui_testing': bool(exe_ok and s.onec_staging_ib_connection and s.onec_test_manager_connection),
                'com_configured': ComConnector(s).configured, 'runtime_writes': False,
                'test_client_connection': bool(s.onec_staging_ib_connection),
                'test_manager_connection': bool(s.onec_test_manager_connection), 'test_port': s.onec_test_port}

    async def run(self, request: dict) -> dict:
        previous = self.read_session()
        if previous and previous['review_state'] == 'pending':
            raise ValueError('Сначала примите или отклоните предыдущие изменения')
        task = str(request.get('task', '')).strip()
        if not task:
            raise ValueError('Задача не указана')
        status = self.doctor()
        if not status['can_run']:
            raise ValueError('Настройте модель и выберите папку с исходниками 1С: ' + '; '.join(status['errors']))
        check = bool(request.get('check', True))
        if check and not status['can_check']:
            raise ValueError('Для проверки нужна отдельная staging-база и установленная 1С')
        s = self.settings
        provider = create_provider(s)
        before = source_bytes(self.workspace)
        self.workspace.baseline = {p: decode(v).replace("\r\n", "\n").replace("\r", "\n")
                                   for p, v in before.items()}
        session: dict[str, Any] = {'id': uuid.uuid4().hex, 'task': task, 'before': before,
                                  'review_state': 'pending', 'status': 'interrupted', 'steps': [],
                                  'summary': 'Процесс прерван. Проверьте изменения или отклоните их.',
                                  'checks_ok': None, 'ui_test_ok': None, 'snapshots': [],
                                  'primary_connection': s.onec_ib_connection,
                                  'staging_connection': s.onec_staging_ib_connection}
        atomic_json(self.path, session)
        cancel_path = config_root() / 'cancel'
        cancel_path.unlink(missing_ok=True)

        def progress(event: dict) -> None:
            if event['type'] == 'tool_end':
                session['steps'].append({k: event[k] for k in ('tool', 'args', 'result')})
                atomic_json(self.path, session)
            self.emit(event)

        harness = HarnessAgent(
            provider, self.workspace, allow_writes=True, execute_checks=check,
            designer=Designer(s, connection_override=s.onec_staging_ib_connection) if check else None,
            runtime=ComConnector(s), scenario_compiler=ScenarioCompiler(s.onec_test_host, s.onec_test_port),
            test_runner=TestManagerRunner(s, self.workspace) if request.get('ui_test') and check else None,
            execute_ui_tests=bool(request.get('ui_test') and check),
            on_event=progress, cancelled=cancel_path.exists,
        )
        context = ''
        if previous and previous['review_state'] == 'accepted':
            context = f"Предыдущая принятая задача: {previous['task']}\nРезультат: {previous['summary']}\n\n"
        try:
            result = await harness.run(context + task, max_steps=60)
            session.update(asdict(result))
        except Exception as exc:
            session.update(status='failed', summary=f'Ошибка: {exc}. Изменения сохранены для review.')
        session['after'] = source_bytes(self.workspace)
        if session['before'] == session['after']:
            session['review_state'] = 'accepted'
        atomic_json(self.path, session)
        return self.review(session)

    def decide(self, decision: str) -> dict:
        session = self.read_session()
        if not session or session['review_state'] != 'pending':
            raise ValueError('Нет ожидающих review изменений')
        current = source_bytes(self.workspace)
        if 'after' in session and current != session['after']:
            raise ValueError('Исходники изменились вне приложения. Автоматический review/откат заблокирован.')
        if decision == 'accept' and (session['status'] != 'completed' or session.get('checks_ok') is False):
            raise ValueError('Незавершённый запуск нельзя принять. Отклоните изменения и повторите задачу.')
        if decision == 'reject':
            before = session['before']
            for name in before.keys() | current.keys():
                path = self.workspace.resolve(name)
                if name not in before:
                    path.unlink(missing_ok=True)
                elif before.get(name) != current.get(name):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(before[name]))
            session['review_state'] = 'rejected'
        else:
            session['review_state'] = 'accepted'
        session.setdefault('after', current)
        atomic_json(self.path, session)
        return self.review(session)

    def apply(self, confirmed: bool) -> dict:
        session = self.read_session()
        if not confirmed:
            raise ValueError('Применение в основной базе требует явного подтверждения')
        if not session or session['review_state'] != 'accepted' or session['status'] != 'completed':
            raise ValueError('Сначала завершите задачу и примите изменения')
        if session.get('checks_ok') is not True:
            raise ValueError('Применение доступно только после успешных проверок в staging')
        if session.get('deployment') == 'applied':
            raise ValueError('Эта задача уже применена')
        if source_bytes(self.workspace) != session.get('after'):
            raise ValueError('Исходники изменились после проверки; требуется новая проверка')
        s = self.settings
        if (session.get('primary_connection') != s.onec_ib_connection
                or session.get('staging_connection') != s.onec_staging_ib_connection):
            raise ValueError('Подключения изменились после проверки; требуется новая задача с проверкой')
        require_test_connection(s.onec_ib_connection, s.onec_staging_ib_connection)
        designer = Designer(s)
        backup = self.workspace.root / '.onec-harness' / 'backups' / f"{session['id']}-{uuid.uuid4().hex[:8]}.dt"
        self.emit({'type': 'tool_start', 'tool': 'backup'})
        result = designer.dump_infobase(backup, execute=True)
        if not result.ok or not backup.exists() or backup.stat().st_size == 0:
            raise ValueError('Резервная копия .dt не создана; применение отменено. ' + result.combined_output())
        session.update(deployment='applying', backup=str(backup))
        atomic_json(self.path, session)
        try:
            files = self.review(session)['files']
            scopes = set()
            for file in files:
                parts = file['path'].split('/')
                scopes.add(parts[1] if len(parts) >= 3 and parts[0] == 'Extensions' else '')
            for scope in sorted(scopes):
                self.emit({'type': 'tool_start', 'tool': 'apply_extension' if scope else 'apply_config'})
                source = self.workspace.resolve(f'Extensions/{scope}') if scope else self.workspace.root
                result = designer.load_config(source, extension=scope or None, execute=True, update_db=True,
                                              update_dump_info=True)
                if not result.ok:
                    raise ValueError(result.combined_output() or '1С не подтвердила применение')
            session['deployment'] = 'applied'
        except Exception as exc:
            session['deployment'] = 'failed'
            atomic_json(self.path, session)
            raise ValueError(f'Применение не завершено. Резервная копия: {backup}. {exc}') from exc
        atomic_json(self.path, session)
        return self.review(session)

    async def dispatch(self, request: dict) -> Any:
        op = request.get('op')
        if op == 'settings':
            return public_config()
        if op == 'save_settings':
            active = self.read_session()
            if active and active['review_state'] == 'pending':
                raise ValueError('Завершите review перед сменой настроек')
            return write_config(request['values'])
        if op == 'doctor':
            return self.doctor()
        if op == 'session':
            return self.review()
        if op == 'test_model':
            response = await create_provider(self.settings).complete([Message(role='user', content='Ответь одним словом: OK')])
            return {'message': response.content}
        if op == 'apply':
            return self.apply(request.get('confirmed') is True)
        if op == 'run':
            return await self.run(request)
        if op in {'accept', 'reject'}:
            return self.decide(op)
        if op == 'export':
            self.workspace.ensure_exists()
            if any(self.workspace.root.iterdir()):
                raise ValueError('Для выгрузки выберите пустую папку, чтобы сохранить существующие исходники')
            connection_identity(self.settings.onec_ib_connection)
            result = Designer(self.settings).dump_config(self.workspace.root, execute=True)
            if not result.ok:
                raise ValueError(result.combined_output() or 'Выгрузка 1С завершилась ошибкой')
            return self.doctor()
        raise ValueError('Unknown desktop operation')


def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')
    try:
        request = json.loads(sys.stdin.readline())
        if request.get('op') == 'cancel':
            config_root().mkdir(parents=True, exist_ok=True)
            (config_root() / 'cancel').touch()
            emit({'type': 'result', 'data': {'message': 'Остановка после текущего вызова'}})
            return
        # OS lock releases even after a crash. Never run two writers concurrently.
        config_root().mkdir(parents=True, exist_ok=True)
        with (config_root() / 'desktop.lock').open('a+b') as lock:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0)
                lock.write(b'0')
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = asyncio.run(DesktopService().dispatch(request))
            emit({'type': 'result', 'data': data})
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        sys.exit(1)


if __name__ == '__main__':
    main()
