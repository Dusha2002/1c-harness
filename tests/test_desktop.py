import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from onec_harness import desktop_bridge
from onec_harness.agent import HarnessAgent
from onec_harness.connections import require_test_connection
from onec_harness.desktop_bridge import DesktopService
from onec_harness.desktop_config import load_settings, public_config, write_config
from onec_harness.discovery import discover_infobases, discover_onec_executables, parse_ibases
from onec_harness.providers.base import LLMResponse, ProviderError
from onec_harness.workspace import Workspace


class ScriptedProvider:
    def __init__(self, actions):
        self.actions = iter(actions)

    async def complete(self, messages):
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        return LLMResponse(json.dumps(action, ensure_ascii=False))


def action(tool, **args):
    return {'tool': tool, 'args': args}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('ONEC_HARNESS_CONFIG_DIR', str(tmp_path / 'settings'))
    workspace = tmp_path / 'sources'
    workspace.mkdir()
    (workspace / 'Модуль.bsl').write_bytes(b'\xef\xbb\xbf' + 'старое\r\n'.encode())
    write_config({'llm_provider': 'openai', 'llm_api_key': 'test-not-a-real-key', 'llm_model': 'test',
                  'onec_workspace': str(workspace)})
    return DesktopService(emit_event=lambda event: None)


def test_run_review_reject_preserves_bytes_without_git(service, monkeypatch):
    original = (service.workspace.root / 'Модуль.bsl').read_bytes()
    provider = ScriptedProvider([
        action('patch', path='Модуль.bsl', old='старое', new='новое'),
        action('diff'), action('finish', summary='Готово'),
    ])
    monkeypatch.setattr(desktop_bridge, 'create_provider', lambda settings: provider)
    result = asyncio.run(service.run({'task': 'Измени код', 'check': False}))
    assert result['status'] == 'completed'
    assert result['files'][0]['original'] == 'старое\r\n'
    assert result['files'][0]['modified'].replace('\r\n', '\n') == 'новое\n'
    assert result['checks_ok'] is None
    with pytest.raises(ValueError, match='предыдущие'):
        asyncio.run(service.run({'task': 'Следующая', 'check': False}))
    service.decide('reject')
    assert (service.workspace.root / 'Модуль.bsl').read_bytes() == original


def test_provider_failure_keeps_recoverable_review(service, monkeypatch):
    provider = ScriptedProvider([action('patch', path='Модуль.bsl', old='старое', new='новое'), ProviderError('offline')])
    monkeypatch.setattr(desktop_bridge, 'create_provider', lambda settings: provider)
    result = asyncio.run(service.run({'task': 'Измени', 'check': False}))
    assert result['status'] == 'failed'
    assert result['files'][0]['modified'].replace('\r\n', '\n') == 'новое\n'
    recovered = DesktopService(emit_event=lambda event: None)
    assert recovered.review()['id'] == result['id']
    with pytest.raises(ValueError, match='Незавершённый'):
        recovered.decide('accept')
    recovered.decide('reject')


def test_review_refuses_to_clobber_external_edit(service, monkeypatch):
    provider = ScriptedProvider([action('patch', path='Модуль.bsl', old='старое', new='новое'),
                                 action('diff'), action('finish')])
    monkeypatch.setattr(desktop_bridge, 'create_provider', lambda settings: provider)
    asyncio.run(service.run({'task': 'Измени', 'check': False}))
    service.workspace.write_text('Модуль.bsl', 'внешняя правка')
    with pytest.raises(ValueError, match='вне приложения'):
        service.decide('reject')
    assert service.workspace.read_text('Модуль.bsl') == 'внешняя правка'


def test_settings_never_return_secrets_and_empty_preserves(service):
    assert 'test-not-a-real-key' not in json.dumps(public_config())
    write_config({'llm_api_key': ''})
    assert load_settings().llm_api_key == 'test-not-a-real-key'
    write_config({'llm_api_key': None})
    assert load_settings().llm_api_key is None


@pytest.mark.parametrize('test', ['/F "c:/BASE/"', '/F "C:\\base\\sub\\.."'])
def test_same_staging_cannot_be_disguised(test):
    with pytest.raises(ValueError, match='differ'):
        require_test_connection('/F "C:\\base"', test)


def test_connection_disallows_extra_designer_actions():
    with pytest.raises(ValueError, match='only'):
        require_test_connection('/F "C:\\base"', '/F "C:\\test" /RestoreIB "evil.dt"')


def test_extension_patch_invalidates_extension_scope(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.write_text('Extensions/Расширение/Module.bsl', 'old')
    harness = HarnessAgent(ScriptedProvider([]), workspace, allow_writes=True)
    harness._execute_tool('patch', {'path': 'Extensions/Расширение/Module.bsl', 'old': 'old', 'new': 'new'})
    assert harness._extension_changed == {'Расширение'}
    assert not harness._config_changed


def test_cancel_never_executes_model_action(service):
    harness = HarnessAgent(ScriptedProvider([]), service.workspace, cancelled=lambda: True)
    assert asyncio.run(harness.run('test')).status == 'cancelled'


def test_apply_requires_validation_and_explicit_confirmation(service):
    with pytest.raises(ValueError, match='подтверждения'):
        service.apply(False)
    with pytest.raises(ValueError, match='завершите'):
        service.apply(True)


def test_agent_cannot_read_secrets_or_patch_internal_state(service):
    agent = HarnessAgent(ScriptedProvider([]), service.workspace, allow_writes=True)
    assert agent._execute_tool('read', {'path': '.env'}).startswith('ERROR:')
    assert agent._execute_tool('patch', {'path': '.onec-harness/anything.xml', 'old': 'a', 'new': 'b'}).startswith('ERROR:')


def test_apply_requires_real_backup_before_loading(service, monkeypatch):
    from onec_harness.desktop_bridge import atomic_json, source_bytes
    from onec_harness.onec.designer import CommandResult

    s = service.settings
    s.onec_ib_connection = '/F "C:\\primary"'
    s.onec_staging_ib_connection = '/F "C:\\staging"'
    state = source_bytes(service.workspace)
    atomic_json(service.path, {'id': 'test', 'task': 'test', 'summary': 'done', 'steps': [],
                              'before': {}, 'after': state, 'checks_ok': True, 'ui_test_ok': None,
                              'status': 'completed', 'review_state': 'accepted',
                              'primary_connection': s.onec_ib_connection,
                              'staging_connection': s.onec_staging_ib_connection})
    calls = []

    class FakeDesigner:
        def __init__(self, settings):
            pass

        def dump_infobase(self, target, execute):
            calls.append('backup')
            return CommandResult(command=[], returncode=0, executed=True)

        def load_config(self, *args, **kwargs):
            calls.append('load')
            raise AssertionError('must not load without a nonempty backup')

    monkeypatch.setattr(desktop_bridge, 'Designer', FakeDesigner)
    with pytest.raises(ValueError, match='не создана'):
        service.apply(True)
    assert calls == ['backup']


def test_new_file_and_deleted_file_review_and_restore(service):
    from onec_harness.desktop_bridge import atomic_json, source_bytes

    before = source_bytes(service.workspace)
    (service.workspace.root / 'Модуль.bsl').unlink()
    service.workspace.write_text('Новый.xml', '<new/>')
    after = source_bytes(service.workspace)
    atomic_json(service.path, {'before': before, 'after': after, 'status': 'failed', 'review_state': 'pending'})
    files = service.review()['files']
    assert any(f['created'] for f in files)
    assert any(f['deleted'] for f in files)
    service.decide('reject')
    assert source_bytes(service.workspace) == before


def test_requested_ui_test_cannot_silently_finish(service):
    agent = HarnessAgent(ScriptedProvider([]), service.workspace, execute_ui_tests=True)
    agent._source_changed = True
    agent._diff_seen = True
    assert 'run_ui_test' in agent._finish_block_reason()


def test_unchanged_crlf_files_do_not_pollute_agent_diff(service, monkeypatch):
    (service.workspace.root / 'Unchanged.bsl').write_bytes(b'unchanged\r\n')
    provider = ScriptedProvider([action('patch', path='Модуль.bsl', old='старое', new='новое'),
                                 action('diff'), action('finish')])
    monkeypatch.setattr(desktop_bridge, 'create_provider', lambda settings: provider)
    result = asyncio.run(service.run({'task': 'Измени', 'check': False}))
    diff = next(step['result'] for step in result['steps'] if step['tool'] == 'diff')
    assert 'Unchanged.bsl' not in diff
    assert len(result['files']) == 1


def test_parse_registered_infobases_supports_file_and_server() -> None:
    entries = parse_ibases(
        '\ufeff[Demo]\nConnect=File="C:\\Bases\\Demo";\n'
        '[ERP]\nConnect=Srvr="srv01";Ref="ERP";\n'
    )
    assert entries == [
        {'name': 'Demo', 'connection': '/F "C:\\Bases\\Demo"', 'file_path': 'C:\\Bases\\Demo'},
        {'name': 'ERP', 'connection': '/S "srv01\\ERP"', 'file_path': None},
    ]


def test_discovery_finds_platform_and_registered_base(tmp_path, monkeypatch) -> None:
    program_files = tmp_path / 'Program Files'
    exe = program_files / '1cv8' / '8.3.25.1000' / 'bin' / '1cv8.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')

    appdata = tmp_path / 'AppData' / 'Roaming'
    registry = appdata / '1C' / '1CEStart' / 'ibases.v8i'
    registry.parent.mkdir(parents=True)
    registry.write_text('[Demo]\nConnect=File="C:\\Bases\\Demo";\n', encoding='utf-8')

    monkeypatch.setenv('ProgramFiles', str(program_files))
    monkeypatch.delenv('ProgramFiles(x86)', raising=False)
    monkeypatch.setenv('APPDATA', str(appdata))

    assert discover_onec_executables() == [str(exe.resolve())]
    assert discover_infobases() == [{'name': 'Demo', 'connection': '/F "C:\\Bases\\Demo"', 'file_path': 'C:\\Bases\\Demo'}]


def test_desktop_service_exposes_discovery(tmp_path, monkeypatch) -> None:
    program_files = tmp_path / 'Program Files'
    exe = program_files / '1cv8' / '8.3.25.1000' / 'bin' / '1cv8.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')

    appdata = tmp_path / 'AppData' / 'Roaming'
    registry = appdata / '1C' / '1CEStart' / 'ibases.v8i'
    registry.parent.mkdir(parents=True)
    registry.write_text('[Demo]\nConnect=File="C:\\Bases\\Demo";\n', encoding='utf-8')

    monkeypatch.setenv('ProgramFiles', str(program_files))
    monkeypatch.delenv('ProgramFiles(x86)', raising=False)
    monkeypatch.setenv('APPDATA', str(appdata))
    monkeypatch.setenv('ONEC_HARNESS_CONFIG_DIR', str(tmp_path / 'settings'))

    result = DesktopService(emit_event=lambda event: None).discover()
    assert result['executables'] == [str(exe.resolve())]
    assert result['infobases'][0]['name'] == 'Demo'
    assert result['suggested_workspace'].endswith('workspace')


def test_workspace_persistent_baseline_supports_git_free_restart(tmp_path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text('Module.bsl', 'before\n')
    workspace.capture_baseline()

    restarted = Workspace(tmp_path)
    restarted.write_text('Module.bsl', 'after\n')

    assert restarted.changed_paths() == ['Module.bsl']
    assert '-before' in restarted.git_diff()
    assert '+after' in restarted.git_diff()
    restarted.git_restore('Module.bsl', confirmed=True)
    assert restarted.read_text('Module.bsl') == 'before\n'


def test_file_infobase_discovery_exposes_copy_source() -> None:
    entries = parse_ibases('[Demo]\nConnect=File="C:\\Bases\\Demo";\n')
    assert entries[0]['file_path'] == 'C:\\Bases\\Demo'


def test_desktop_can_import_and_delete_user_skill(service, tmp_path) -> None:
    source = tmp_path / "review-skill.md"
    source.write_text(
        "---\n"
        "name: review-1c\n"
        "description: Проверяет изменения перед применением\n"
        "---\n\n"
        "Сначала проверь инварианты и diff.\n",
        encoding="utf-8",
    )

    added = service.import_skill(str(source))
    assert added["name"] == "review-1c"
    assert any(item["name"] == "review-1c" and item["source"] == "user" for item in service.skill_list())

    skills = service.delete_skill("review-1c")
    assert all(item["name"] != "review-1c" for item in skills)


def test_desktop_exposes_builtin_onec_skill(service) -> None:
    skills = service.skill_list()
    assert any(item["name"] == "onec-engineering" and item["source"] == "builtin" for item in skills)


def test_discovery_scans_are_explicit_and_independent(service, monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        desktop_bridge,
        'discover_onec_executables',
        lambda: calls.append('platforms') or ['C:\\Program Files\\1cv8\\bin\\1cv8.exe'],
    )
    monkeypatch.setattr(
        desktop_bridge,
        'discover_infobases',
        lambda: calls.append('bases') or [{'name': 'Demo', 'connection': '/F "C:\\Demo"', 'file_path': 'C:\\Demo'}],
    )

    platform_result = service.discover_platforms()
    assert calls == ['platforms']
    assert platform_result['executables']

    calls.clear()
    base_result = service.discover_bases()
    assert calls == ['bases']
    assert base_result['infobases'][0]['name'] == 'Demo'


def test_doctor_counts_sources_without_reading_contents(service, monkeypatch) -> None:
    def fail_source_texts():
        raise AssertionError('doctor must not read all source contents')

    monkeypatch.setattr(service.workspace, 'source_texts', fail_source_texts)
    status = service.doctor()

    assert status['source_count'] >= 1


def test_bridge_server_handles_multiple_requests_without_restart(tmp_path: Path) -> None:
    env = os.environ.copy()
    env['ONEC_HARNESS_CONFIG_DIR'] = str(tmp_path / 'config')
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(root / 'src') + os.pathsep + env.get('PYTHONPATH', '')

    process = subprocess.Popen(
        [sys.executable, '-m', 'onec_harness.desktop_bridge', '--server'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        for request_data in ({'op': 'settings'}, {'op': 'discovery_defaults'}):
            process.stdin.write(json.dumps(request_data) + '\n')
            process.stdin.flush()
            event = json.loads(process.stdout.readline())
            assert event['type'] == 'result'
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_prepare_staging_creates_empty_sandbox_and_loads_config(service, monkeypatch, tmp_path) -> None:
    from onec_harness.onec.designer import CommandResult

    primary = '/F "C:\\RealData"'
    service.settings.onec_ib_connection = primary
    service.settings.onec_exe = tmp_path / '1cv8.exe'
    service.settings.onec_exe.write_bytes(b'')
    service.workspace.write_text('Configuration.xml', '<MetaDataObject/>')

    calls: list[tuple[str, str]] = []

    class FakeDesigner:
        def __init__(self, settings, connection_override=None):
            self.connection_override = connection_override

        def create_file_infobase(self, target, execute=False):
            calls.append(('create', str(target)))
            target.mkdir(parents=True, exist_ok=True)
            (target / '1Cv8.1CD').write_bytes(b'sandbox')
            return CommandResult(command=[], returncode=0, executed=True)

        def load_config(self, source, **kwargs):
            calls.append(('load', self.connection_override or 'primary'))
            return CommandResult(command=[], returncode=0, executed=True)

    monkeypatch.setattr(desktop_bridge, 'Designer', FakeDesigner)
    result = service.prepare_staging(str(tmp_path / 'sandbox'))

    assert result['mode'] == 'empty_sandbox'
    assert result['primary_data_access'] == 'read_only'
    assert calls[0][0] == 'create'
    assert calls[1][0] == 'load'
    assert calls[1][1] == result['connection']
    assert 'RealData' not in result['path']


def test_runtime_data_access_stays_on_primary_connection(service) -> None:
    service.settings.onec_ib_connection = '/F "C:\\UserData"'
    service.settings.onec_staging_ib_connection = '/F "C:\\Sandbox"'

    from onec_harness.onec.com import derive_com_connection_string

    connection = derive_com_connection_string(service.settings)

    assert 'UserData' in connection
    assert 'Sandbox' not in connection
