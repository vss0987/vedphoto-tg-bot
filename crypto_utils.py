"""
Гибридное (асимметричное) шифрование файлов бэкапов/архивов.

Схема: сервер шифрует ПУБЛИЧНЫМ ключом (public_key.pem) и физически не
может расшифровать обратно тем же ключом — для расшифровки нужен
ПРИВАТНЫЙ ключ (private_key.pem), которого на сервере нет и не должно
быть никогда. Это отличает данную схему от простого симметричного
шифрования (один ключ и шифрует, и расшифровывает) — там ключ на сервере
даёт возможность и зашифровать, и расшифровать, а значит при компрометации
сервера защиты по сути нет. Здесь сервер умеет только "закрывать замок",
открыть его может только тот, у кого есть отдельный, никогда не
покидающий локальную машину, приватный ключ.

Технически — RSA-OAEP (для одноразового симметричного ключа) поверх
Fernet (для самих данных, т.к. RSA умеет шифровать только очень короткие
сообщения):
1. Генерируется одноразовый Fernet-ключ.
2. Им шифруются сами данные (быстро, без ограничений на размер).
3. Одноразовый ключ шифруется публичным RSA-ключом.
4. Результат — JSON с обоими зашифрованными частями.

Требуется пакет cryptography:
    pip install cryptography --break-system-packages

Ключи создаются один раз ЛОКАЛЬНО (НЕ на сервере!):
    python scripts/generate_keypair.py
Дальше: public_key.pem — скопировать на сервер (не секретный).
        private_key.pem — остаётся только локально, никогда не деплоится
        и не коммитится в git.
"""

import base64
import json
import logging
import os

from dotenv import load_dotenv

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import serialization, hashes
except ImportError:  # pragma: no cover
    Fernet = None
    padding = None
    serialization = None
    hashes = None

load_dotenv()

logger = logging.getLogger(__name__)

# Пути к ключам. На сервере обязан быть только public_key.pem — если
# private_key.pem там тоже вдруг окажется, вся схема теряет смысл, поэтому
# держите PRIVATE_KEY_PATH в .gitignore и никогда не копируйте этот файл
# при деплое.
PUBLIC_KEY_PATH = os.getenv("PUBLIC_KEY_PATH", "public_key.pem")
PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH", "private_key.pem")

_OAEP_PADDING = None
if padding is not None:
    _OAEP_PADDING = padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def is_encryption_configured() -> bool:
    """Готов ли сервер ШИФРОВАТЬ (нужен только публичный ключ)."""
    return Fernet is not None and os.path.isfile(PUBLIC_KEY_PATH)


def is_decryption_configured() -> bool:
    """Доступна ли РАСШИФРОВКА на этой машине (нужен приватный ключ —
    на сервере в норме всегда False, и это правильно)."""
    return Fernet is not None and os.path.isfile(PRIVATE_KEY_PATH)


def _load_public_key():
    with open(PUBLIC_KEY_PATH, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def _load_private_key():
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def encrypt_bytes(data: bytes) -> bytes:
    """Шифрует данные публичным ключом. Это единственное, что можно
    сделать без приватного ключа — на сервере, где его нет, других
    вариантов и не предусмотрено."""
    if Fernet is None:
        raise RuntimeError(
            "Пакет 'cryptography' не установлен. "
            "Выполните: pip install cryptography --break-system-packages"
        )
    if not os.path.isfile(PUBLIC_KEY_PATH):
        raise RuntimeError(
            f"Не найден публичный ключ ({PUBLIC_KEY_PATH}). Сгенерируйте пару "
            f"ключей ЛОКАЛЬНО: python scripts/generate_keypair.py — и скопируйте "
            f"public_key.pem на сервер (это не секрет, приватный ключ копировать не нужно)."
        )

    public_key = _load_public_key()

    one_time_key = Fernet.generate_key()
    encrypted_data = Fernet(one_time_key).encrypt(data)
    encrypted_key = public_key.encrypt(one_time_key, _OAEP_PADDING)

    envelope = {
        "key": base64.b64encode(encrypted_key).decode(),
        "data": base64.b64encode(encrypted_data).decode(),
    }
    return json.dumps(envelope).encode()


def decrypt_bytes(token: bytes) -> bytes:
    """Расшифровывает — требует приватный ключ. На сервере, где его нет,
    эта функция всегда будет падать с понятной ошибкой — это ожидаемо
    и является сутью защиты, а не багом."""
    if Fernet is None:
        raise RuntimeError(
            "Пакет 'cryptography' не установлен. "
            "Выполните: pip install cryptography --break-system-packages"
        )
    if not os.path.isfile(PRIVATE_KEY_PATH):
        raise RuntimeError(
            f"Не найден приватный ключ ({PRIVATE_KEY_PATH}). Это ожидаемо на "
            f"сервере — расшифровка возможна только на локальной машине, где "
            f"он есть. Скопируйте зашифрованный файл на локальную машину и "
            f"запустите scripts/decrypt_archive.py там."
        )

    private_key = _load_private_key()
    envelope = json.loads(token.decode())
    encrypted_key = base64.b64decode(envelope["key"])
    encrypted_data = base64.b64decode(envelope["data"])

    one_time_key = private_key.decrypt(encrypted_key, _OAEP_PADDING)
    return Fernet(one_time_key).decrypt(encrypted_data)
