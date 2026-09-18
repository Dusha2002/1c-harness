"""Desktop settings: per-user, atomic, Windows DPAPI encryption at rest."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

from onec_harness.settings import Settings

SECRET_FIELDS = {
    'llm_api_key', 'gigachat_credentials', 'anthropic_api_key', 'onec_password',
    'onec_test_manager_password', 'onec_com_connection',
}
EDITABLE = {
    'llm_provider', 'llm_model', 'llm_api_key', 'gigachat_credentials', 'gigachat_scope',
    'anthropic_api_key', 'openai_compatible_base_url', 'gigachat_ca_bundle', 'onec_exe',
    'onec_workspace', 'onec_ib_connection', 'onec_staging_ib_connection', 'onec_user', 'onec_password',
    'onec_com_connection', 'onec_test_manager_connection', 'onec_test_manager_user', 'onec_test_manager_password',
}


def config_root() -> Path:
    base = os.environ.get('ONEC_HARNESS_CONFIG_DIR')
    if base:
        return Path(base).expanduser().resolve()
    return Path(os.environ.get('APPDATA', str(Path.home() / '.config'))) / '1C-Harness'


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Windows could not protect/unprotect desktop credentials')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def read_config() -> dict:
    path = config_root() / 'settings.dat'
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if raw.startswith(b'DPAPI\0'):
        raw = _dpapi(raw[6:], False)
    return json.loads(raw.decode('utf-8'))


def write_config(values: dict) -> dict:
    if set(values) - EDITABLE:
        raise ValueError('Unknown settings fields')
    current = read_config()
    for key, value in values.items():
        if key in SECRET_FIELDS and value == '':
            continue  # Empty form fields preserve saved credentials; null clears them.
        current[key] = value
    Settings(**current)  # Validate before replacing the stored configuration.
    root = config_root()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(current, ensure_ascii=False).encode('utf-8')
    if os.name == 'nt':
        raw = b'DPAPI\0' + _dpapi(raw, True)
    path = root / 'settings.tmp'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    os.replace(path, root / 'settings.dat')
    return public_config()


def load_settings() -> Settings:
    return Settings(**read_config())


def public_config() -> dict:
    values = load_settings().model_dump(mode='json')
    return {'values': {key: values[key] for key in sorted(EDITABLE - SECRET_FIELDS)},
            'secrets': {key: bool(values[key]) for key in sorted(SECRET_FIELDS)}}
