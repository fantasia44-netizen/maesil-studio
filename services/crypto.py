"""API 키 등 민감값 암호화/복호화"""
import os
import base64
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.environ.get('ENCRYPTION_KEY', '')
    if not key:
        key = Fernet.generate_key().decode()
    if len(key) < 32:
        key = key.ljust(32, '0')
    key_bytes = base64.urlsafe_b64encode(key[:32].encode())
    return Fernet(key_bytes)


def encrypt_value(plaintext: str) -> str:
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
