"""One JSON request on stdin, NDJSON progress and final response on stdout."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from onec_harness.agent import HarnessAgent
from onec_harness.connections import connection_identity, require_test_connection
from onec_harness.desktop_config import config_root, load_settings, public_config, write_config
from onec_harness.discovery import discover_infobases, discover_onec_executables
from onec_harness.onec.com import ComConnector
from onec_harness.onec.designer import Designer
from onec_harness.onec.e2e import TestManagerRunner
from onec_harness.onec.testing import ScenarioCompiler
from onec_harness.providers.base import Message
from onec_harness.providers.factory import create_provider
from onec_harness.skills import SkillStore
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


def _friendly_onec_error(output: str, *, target: str) -> str:
    text = output.strip()
    normalized = text.casefold()
    if 'пользователь иб не идентифицирован' in normalized:
        if target == 'primary':
            return (
                '1С не идентифицировала пользователя рабочей базы. '
                'Укажите точное имя пользователя ИБ и пароль в разделе «Рабочая база». '
                'Если в 1С пароль пустой, оставьте поле пароля пустым.'
            )
        return (
            'Sandbox-база запросила пользователя ИБ. Для автоматически созданной sandbox '
            'Harness должен подключаться без учётных данных; пересоздайте sandbox или укажите '
            'отдельного пользователя staging в расширенных настройках.'
        )
    if 'неверное имя или пароль' in normalized or 'неверный пароль' in normalized:
        return '1С отклонила имя пользователя или пароль. Проверьте учётные данные информационной базы.'
    return text or '1С завершила команду с ошибкой'


class DesktopService:
    def __init__(self, emit_event=emit):
        self.settings = load_settings()
        self.workspace = Workspace(self.settings.onec_workspace)
        self.path = self.workspace.root / '.onec-harness' / 'desktop-session.json'
        self.emit = emit_event
        self.skills = SkillStore(config_root() / 'skills')

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

    def discovery_defaults(self) -> dict:
        return {"suggested_workspace": str((config_root() / "workspace").resolve())}

    def discover_platforms(self) -> dict:
        return {
            "executables": discover_onec_executables(),
            **self.discovery_defaults(),
        }

    def discover_bases(self) -> dict:
        return {
            "infobases": discover_infobases(),
            **self.discovery_defaults(),
        }

    def discover(self) -> dict:
        """Compatibility aggregate; desktop UI intentionally uses explicit scans."""
        return {
            **self.discover_platforms(),
            **self.discover_bases(),
        }

    def prepare_staging(self, target: str | None = None) -> dict:
        """Create/reuse an empty local sandbox and load the exported configuration into it.

        Real business data stays in the user's primary infobase and remains available to
        the agent through the read-only COM runtime. Staging exists only for validating
        changed configuration code and therefore does not copy the user's data files.
        """
        if not self.settings.onec_ib_connection.strip():
            raise ValueError('Сначала выберите основную базу 1С')
        if self.workspace.source_count() == 0:
            raise ValueError('Сначала выгрузите конфигурацию основной базы в рабочее пространство')

        identity = connection_identity(self.settings.onec_ib_connection)
        digest = hashlib.sha256(f'{identity[0]}:{identity[1]}'.encode()).hexdigest()[:12]
        target_path = (
            Path(target).expanduser().resolve()
            if target
            else (config_root() / 'staging' / digest).resolve()
        )
        connection = f'/F "{target_path}"'
        require_test_connection(self.settings.onec_ib_connection, connection)

        database_file = target_path / '1Cv8.1CD'
        existing = database_file.exists()
        if target_path.exists() and not existing:
            try:
                nonempty = any(target_path.iterdir())
            except OSError as exc:
                raise ValueError(f'Не удалось проверить папку sandbox: {exc}') from exc
            if nonempty:
                raise ValueError(
                    f'Папка sandbox занята посторонними файлами: {target_path}. '
                    'Выберите другой путь в расширенных настройках.'
                )

        if not existing:
            created = Designer(self.settings, auth_kind='none').create_file_infobase(target_path, execute=True)
            if not created.ok:
                raise ValueError(_friendly_onec_error(created.combined_output(), target='staging'))
            if not database_file.exists():
                raise ValueError('1С завершила создание без ошибки, но файл sandbox-базы 1Cv8.1CD не найден')

        staged = Designer(self.settings, connection_override=connection, auth_kind='staging').load_config(
            self.workspace.root,
            execute=True,
            update_db=True,
        )
        if not staged.ok:
            raise ValueError(_friendly_onec_error(staged.combined_output(), target='staging'))

        write_config({'onec_staging_ib_connection': connection})
        return {
            'connection': connection,
            'path': str(target_path),
            'existing': existing,
            'mode': 'empty_sandbox',
            'primary_data_access': 'read_only',
        }

    def export_sources(self) -> dict:
        """Export primary configuration without exposing a half-written workspace."""
        self.workspace.ensure_exists()
        connection_identity(self.settings.onec_ib_connection)

        visible = [item for item in self.workspace.root.iterdir() if item.name != '.onec-harness']
        default_workspace = (config_root() / 'workspace').resolve()
        recover_partial = (
            bool(visible)
            and self.workspace.root == default_workspace
            and not self.workspace.baseline_root.exists()
        )
        if visible and not recover_partial:
            raise ValueError(
                'Рабочая папка уже содержит файлы. Выберите пустую папку или завершите review, '
                'чтобы Harness не перезаписал существующие исходники.'
            )

        cancel_path = config_root() / 'cancel'
        cancel_path.unlink(missing_ok=True)
        config_root().mkdir(parents=True, exist_ok=True)
        last_count_at = -10.0
        last_count = 0

        with tempfile.TemporaryDirectory(prefix='export-', dir=config_root()) as temp_dir:
            export_root = Path(temp_dir) / 'sources'
            export_root.mkdir(parents=True)

            def progress(elapsed: float) -> None:
                nonlocal last_count_at, last_count
                if elapsed - last_count_at >= 2.0 or last_count_at < 0:
                    last_count = sum(1 for path in export_root.rglob('*') if path.is_file())
                    last_count_at = elapsed
                seconds = int(elapsed)
                self.emit({
                    'type': 'operation_progress',
                    'operation': 'export',
                    'elapsed_seconds': round(elapsed, 1),
                    'files': last_count,
                    'message': (
                        f'1С выгружает конфигурацию · {seconds // 60:02d}:{seconds % 60:02d} '
                        f'· файлов: {last_count}'
                    ),
                })

            result = Designer(self.settings).dump_config(
                export_root,
                execute=True,
                progress=progress,
                cancelled=cancel_path.exists,
                timeout_seconds=max(self.settings.onec_command_timeout_seconds, 1800.0),
            )
            cancel_path.unlink(missing_ok=True)
            if not result.ok:
                raise ValueError(_friendly_onec_error(result.combined_output(), target='primary'))

            exported = Workspace(export_root)
            source_count = exported.source_count()
            if source_count == 0:
                raise ValueError('1С завершила выгрузку, но не создала XML/BSL исходники конфигурации')

            # Only after a complete successful export do we replace a partial
            # first-run export in the app-owned default workspace.
            if recover_partial:
                for item in visible:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink(missing_ok=True)

            for item in export_root.iterdir():
                shutil.move(str(item), str(self.workspace.root / item.name))

        self.workspace.capture_baseline()
        self.emit({
            'type': 'operation_progress',
            'operation': 'export',
            'elapsed_seconds': None,
            'files': self.workspace.source_count(),
            'message': 'Конфигурация выгружена',
        })
        return self.doctor()

    def skill_list(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.skills.list()]

    def import_skill(self, path: str) -> dict[str, Any]:
        return asdict(self.skills.import_file(path))

    def delete_skill(self, name: str) -> list[dict[str, Any]]:
        self.skills.delete(name)
        return self.skill_list()

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
        source_count = self.workspace.source_count()
        exe_ok = bool(s.onec_exe and s.onec_exe.is_file())
        return {'llm_provider': s.llm_provider, 'llm_model': s.llm_model, 'llm_credentials': credentials,
                'onec_exe': str(s.onec_exe) if s.onec_exe else None, 'exe_exists': exe_ok,
                'onec_connection': bool(s.onec_ib_connection), 'staging_connection': bool(s.onec_staging_ib_connection),
                'workspace': str(self.workspace.root), 'source_count': source_count, 'errors': errors,
                'can_run': bool(credentials and source_count and not errors),
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
            designer=Designer(s, connection_override=s.onec_staging_ib_connection, auth_kind='staging') if check else None,
            runtime=ComConnector(s), scenario_compiler=ScenarioCompiler(s.onec_test_host, s.onec_test_port),
            test_runner=TestManagerRunner(s, self.workspace) if request.get('ui_test') and check else None,
            execute_ui_tests=bool(request.get('ui_test') and check),
            on_event=progress, cancelled=cancel_path.exists, skills=self.skills,
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
        if op == 'bootstrap':
            return {'doctor': self.doctor(), 'session': self.review()}
        if op == 'settings':
            return public_config()
        if op == 'save_settings':
            active = self.read_session()
            if active and active['review_state'] == 'pending':
                raise ValueError('Завершите review перед сменой настроек')
            return write_config(request['values'])
        if op == 'doctor':
            return self.doctor()
        if op == 'discover':
            return self.discover()
        if op == 'discovery_defaults':
            return self.discovery_defaults()
        if op == 'discover_platforms':
            return self.discover_platforms()
        if op == 'discover_bases':
            return self.discover_bases()
        if op == 'skills':
            return self.skill_list()
        if op == 'import_skill':
            return self.import_skill(str(request.get('path', '')))
        if op == 'delete_skill':
            return self.delete_skill(str(request.get('name', '')))
        if op == 'prepare_staging':
            return self.prepare_staging(request.get('target'))
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
            return self.export_sources()
        raise ValueError('Unknown desktop operation')


def handle_request(request: dict) -> None:
    if request.get('op') == 'cancel':
        config_root().mkdir(parents=True, exist_ok=True)
        (config_root() / 'cancel').touch()
        emit({'type': 'result', 'data': {'message': 'Остановка после текущего вызова'}})
        return

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


def _configure_stdio() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')


def main() -> None:
    _configure_stdio()
    server_mode = '--server' in sys.argv
    if server_mode:
        for raw in sys.stdin:
            if not raw.strip():
                continue
            try:
                handle_request(json.loads(raw))
            except Exception as exc:
                emit({'type': 'error', 'message': str(exc)})
        return

    try:
        raw = sys.stdin.readline()
        if not raw:
            raise ValueError('Desktop bridge received no request')
        handle_request(json.loads(raw))
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        sys.exit(1)


if __name__ == '__main__':
    main()
