import pytest
from cryptography.fernet import Fernet

from app import credential_crypto as cc


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    ciphertext = cc.encrypt_credential("test_baemin_id", "test-pass-123!")
    assert "test-pass-123" not in ciphertext

    decrypted = cc.decrypt_credential(ciphertext)
    assert decrypted == {"login_id": "test_baemin_id", "password": "test-pass-123!"}


def test_encrypt_without_key_raises(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(cc.CredentialCryptoError):
        cc.encrypt_credential("id", "pw")


def test_decrypt_with_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = cc.encrypt_credential("id", "pw")

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(cc.CredentialCryptoError):
        cc.decrypt_credential(ciphertext)


def test_decrypt_garbage_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(cc.CredentialCryptoError):
        cc.decrypt_credential("not-a-real-token")
