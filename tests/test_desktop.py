import asyncio
import json

import pytest

from onec_harness import desktop_bridge
from onec_harness.agent import HarnessAgent
from onec_harness.connections import require_test_connection
from onec_harness.desktop_bridge import DesktopService
from onec_harness.desktop_config import load_settings, public_config, write_config
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
    assert result['files'][0]['modified'] == 'новое\n'
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
    assert result['files'][0]['modified'] == 'новое\n'
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
