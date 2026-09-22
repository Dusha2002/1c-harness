import hashlib
import ssl
import sys
from types import SimpleNamespace

import httpx

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
