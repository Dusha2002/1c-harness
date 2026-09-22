import asyncio
import hashlib
import ssl
import sys
from types import SimpleNamespace

import httpx
import pytest

from onec_harness.providers.base import ProviderError
from onec_harness.providers.gigachat import GigaChatProvider
from onec_harness.providers.russian_trusted_ca import (
    RUSSIAN_TRUSTED_ROOT_CA_PEM,
    RUSSIAN_TRUSTED_ROOT_CA_SHA256,
)
from onec_harness.settings import Settings


def provider(**kwargs) -> GigaChatProvider:
    settings = Settings(_env_file=None, gigachat_credentials="test", **kwargs)
    return GigaChatProvider(settings)


def test_bundled_root_has_pinned_fingerprint() -> None:
    der = ssl.PEM_cert_to_DER_cert(RUSSIAN_TRUSTED_ROOT_CA_PEM)

    assert hashlib.sha256(der).hexdigest() == RUSSIAN_TRUSTED_ROOT_CA_SHA256
    assert RUSSIAN_TRUSTED_ROOT_CA_SHA256 == (
        "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
    )


def test_verify_loads_bundled_root(monkeypatch) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = provider()
    monkeypatch.setattr(client, "_system_ssl_context", lambda: context)

    assert client._verify() is context
    fingerprints = {
        hashlib.sha256(cert).hexdigest()
        for cert in context.get_ca_certs(binary_form=True)
    }
    assert RUSSIAN_TRUSTED_ROOT_CA_SHA256 in fingerprints


def test_custom_ca_bundle_is_added_to_context(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / "extra-root.pem"
    bundle.write_text(RUSSIAN_TRUSTED_ROOT_CA_PEM, encoding="utf-8")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = provider(gigachat_ca_bundle=bundle)
    monkeypatch.setattr(client, "_system_ssl_context", lambda: context)

    assert client._verify() is context


def test_ssl_verification_can_still_be_explicitly_disabled() -> None:
    assert provider(gigachat_verify_ssl=False)._verify() is False


def test_windows_uses_native_truststore(monkeypatch) -> None:
    marker = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    fake = SimpleNamespace(SSLContext=lambda protocol: marker)
    monkeypatch.setitem(sys.modules, "truststore", fake)
    monkeypatch.setattr("onec_harness.providers.gigachat.sys.platform", "win32")

    assert provider()._verify() is marker


def test_windows_tls_error_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr("onec_harness.providers.gigachat.sys.platform", "win32")
    error = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate in certificate chain"
    )

    message = str(provider()._tls_error("GigaChat OAuth failed", error))

    assert "Russian Trusted Root CA" in message
    assert "Windows trust store" in message
    assert "not disabled automatically" in message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows truststore integration")
def test_real_windows_truststore_accepts_bundled_root() -> None:
    context = provider()._verify()

    assert context is not False


def test_credentials_accept_basic_prefix_and_whitespace() -> None:
    assert GigaChatProvider._normalized_credentials("  Basic abc123==\n") == "abc123=="
    assert GigaChatProvider._normalized_credentials("Bearer abc123==") == "abc123=="


def test_scope_candidates_try_configured_then_other_official_scopes() -> None:
    client = provider(gigachat_scope="GIGACHAT_API_B2B")

    assert client._scope_candidates() == [
        "GIGACHAT_API_B2B",
        "GIGACHAT_API_PERS",
        "GIGACHAT_API_CORP",
    ]


def test_oauth_automatically_recovers_from_scope_mismatch(monkeypatch) -> None:
    responses = [
        httpx.Response(
            400,
            json={"code": 7, "message": "scope from db not fully includes consumed scope"},
        ),
        httpx.Response(
            200,
            json={"access_token": "token", "expires_at": 9999999999999},
        ),
    ]
    posted_scopes: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, data=None, json=None):
            posted_scopes.append(data["scope"])
            return responses.pop(0)

    monkeypatch.setattr("onec_harness.providers.gigachat.httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = provider(gigachat_scope="GIGACHAT_API_PERS")
    monkeypatch.setattr(client, "_verify", lambda: False)

    token = asyncio.run(client._get_access_token())

    assert token == "token"
    assert posted_scopes == ["GIGACHAT_API_PERS", "GIGACHAT_API_B2B"]
    assert client._resolved_scope == "GIGACHAT_API_B2B"


def test_oauth_error_surfaces_gigachat_code_and_message(monkeypatch) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, data=None, json=None):
            return httpx.Response(400, json={"code": 4, "message": "Can't decode 'Authorization' header"})

    monkeypatch.setattr("onec_harness.providers.gigachat.httpx.AsyncClient", lambda **kwargs: FakeClient())
    client = provider()
    monkeypatch.setattr(client, "_verify", lambda: False)

    with pytest.raises(ProviderError, match="code=4"):
        asyncio.run(client._get_access_token())
