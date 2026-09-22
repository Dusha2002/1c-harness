import ssl
import sys
from types import SimpleNamespace

import httpx

from onec_harness.providers.gigachat import GigaChatProvider
from onec_harness.settings import Settings


def provider(**kwargs) -> GigaChatProvider:
    settings = Settings(_env_file=None, gigachat_credentials="test", **kwargs)
    return GigaChatProvider(settings)


def test_custom_ca_bundle_has_priority(tmp_path) -> None:
    bundle = tmp_path / "corp-root.pem"
    bundle.write_text("dummy", encoding="utf-8")

    assert provider(gigachat_ca_bundle=bundle)._verify() == str(bundle)


def test_ssl_verification_can_still_be_explicitly_disabled() -> None:
    assert provider(gigachat_verify_ssl=False)._verify() is False


def test_windows_uses_native_truststore(monkeypatch) -> None:
    marker = ssl.create_default_context()
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

    assert "Windows system trust store" in message
    assert "Trusted Root Certification Authorities" in message
    assert "not disabled automatically" in message
