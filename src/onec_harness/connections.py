"""Validate the deliberately small connection syntax used by automated runners."""
from __future__ import annotations

import ntpath
import re


def connection_identity(raw: str) -> tuple[str, str]:
    match = re.fullmatch(r'\s*/([FS])\s*(?:"([^"\r\n]+)"|([^\s"]+))\s*', raw, re.IGNORECASE)
    if not match:
        raise ValueError('Use only /F "C:\\path\\base" or /S "server\\base"; credentials have separate fields')
    kind = match[1].upper()
    target = match[2] or match[3]
    if kind == 'F':
        target = ntpath.normpath(target)
        if not ntpath.isabs(target):
            raise ValueError('Infobase file path must be absolute')
    return kind, target.replace('/', '\\').rstrip('\\').casefold()


def require_test_connection(primary: str, test: str) -> None:
    target = connection_identity(test)
    if primary.strip() and connection_identity(primary) == target:
        raise ValueError('Test/staging infobase must differ from the primary infobase')
