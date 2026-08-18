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
def _clean_path(raw: str | None) -> str | None:
    """Убирает пробелы/переносы строк и случайно попавшие кавычки вокруг
    значения — та же история, что и раньше с ключом: в .env на Windows
    легко случайно вставить лишний пробел или кавычки при копировании."""
    if raw is None:
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value or None


PUBLIC_KEY_PATH = _clean_path(os.getenv("PUBLIC_KEY_PATH")) or "public_key.pem"
PRIVATE_KEY_PATH = _clean_path(os.getenv("PRIVATE_KEY_PATH")) or "private_key.pem"


def _missing_key_message(path: str, key_kind: str) -> str:
    """Подробное сообщение при отсутствии ключа: показывает, где именно
    искали (абсолютный путь — частая причина путаницы, если .env указывает
    не туда, куда кажется), и что реально лежит рядом, если папка есть."""
    abs_path = os.path.abspath(path)
    lines = [f"Не найден {key_kind} ключ.", f"Искали по пути: {abs_path}"]

    directory = os.path.dirname(abs_path) or "."
    if os.path.isdir(directory):
        found = [f for f in os.listdir(directory) if f.lower().endswith(".pem")]
        if found:
            lines.append(f"В папке {directory} есть файлы .pem: {', '.join(found)}")
            lines.append("Проверьте точное имя файла и путь в .env (регистр, расширение, лишние пробелы).")
        else:
            lines.append(f"В папке {directory} вообще нет файлов .pem.")
    else:
        lines.append(f"Такой папки не существует: {directory}")

    return "\n".join(lines)

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
            _missing_key_message(PUBLIC_KEY_PATH, "публичный")
            + "\nСгенерируйте пару ключей ЛОКАЛЬНО: python scripts/generate_keypair.py "
            "— и скопируйте public_key.pem на сервер (это не секрет, приватный ключ копировать не нужно)."
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
            _missing_key_message(PRIVATE_KEY_PATH, "приватный")
            + "\nЭто ожидаемо на сервере — расшифровка возможна только на локальной "
            "машине, где он есть. Скопируйте зашифрованный файл на локальную "
            "машину и запустите scripts/decrypt_archive.py там."
        )

    private_key = _load_private_key()
    envelope = json.loads(token.decode())
    encrypted_key = base64.b64decode(envelope["key"])
    encrypted_data = base64.b64decode(envelope["data"])

    one_time_key = private_key.decrypt(encrypted_key, _OAEP_PADDING)
    return Fernet(one_time_key).decrypt(encrypted_data)


# =====================================================================
# Шифрование отдельных полей в самой БД (users.phone/full_name/username,
# orders.full_name/notes, questions.username/text) — ДРУГОЙ ключ и ДРУГАЯ
# модель угроз, чем всё, что выше.
#
# Всё выше (encrypt_bytes/decrypt_bytes) защищает архивы, которые сервер
# сам никогда не читает обратно — поэтому там можно позволить себе схему
# "сервер шифрует, но расшифровать не может".
#
# А вот users.phone бот ОБЯЗАН уметь прочитать сам во время работы
# (показать админу, проверить есть ли уже номер и т.п.) — значит ключ для
# этого ОБЯЗАН быть доступен серверу, симметричная схема тут неизбежна.
# Это защищает от утечки ФАЙЛА базы отдельно от сервера (слитый бэкап,
# случайно расшаренный файл, украденный диск без доступа к работающему
# процессу) — но НЕ от полностью скомпрометированного live-сервера, где
# этот ключ лежит в том же .env, что читает и сам бот. Честно предупреждаю
# об этом ограничении явно, чтобы не создавать ложного чувства защиты.
# =====================================================================

DB_FIELD_KEY = _clean_path(os.getenv("DB_FIELD_ENCRYPTION_KEY"))


def is_field_encryption_configured() -> bool:
    return Fernet is not None and bool(DB_FIELD_KEY)


def _field_fernet():
    if Fernet is None:
        raise RuntimeError(
            "Пакет 'cryptography' не установлен. "
            "Выполните: pip install cryptography --break-system-packages"
        )
    if not DB_FIELD_KEY:
        raise RuntimeError(
            "DB_FIELD_ENCRYPTION_KEY не задан в .env. Сгенерируйте: "
            "python scripts/generate_db_field_key.py — и добавьте в .env "
            "НА СЕРВЕРЕ. В отличие от public_key.pem/private_key.pem, этот "
            "ключ должен быть доступен самому боту при работе — храните "
            "его как обычный секрет, рядом с BOT_TOKEN."
        )
    try:
        return Fernet(DB_FIELD_KEY.encode())
    except ValueError as e:
        raise RuntimeError(
            f"DB_FIELD_ENCRYPTION_KEY повреждён (после очистки пробелов — "
            f"{len(DB_FIELD_KEY)} символ(ов), корректный ключ Fernet — это "
            f"ровно 44 символа). Сгенерируйте заново: "
            f"python scripts/generate_db_field_key.py"
        ) from e


def encrypt_field(value: str | None) -> str | None:
    """Шифрует одно строковое поле перед записью в БД. None остаётся
    None — отсутствие значения не оборачиваем в шифротекст."""
    if value is None:
        return None
    return _field_fernet().encrypt(value.encode()).decode()


def decrypt_field(value: str | None) -> str | None:
    """Расшифровывает поле, прочитанное из БД."""
    if not value:
        return value
    return _field_fernet().decrypt(value.encode()).decode()


def decrypt_field_safe(value: str | None) -> str | None:
    """Как decrypt_field, но не падает на значениях, которые ещё не
    зашифрованы (легаси-данные, записанные до включения шифрования полей,
    либо шифрование полей вообще выключено) — просто возвращает как есть.
    Нужно, чтобы старые записи не ломали чтение, пока не прогнан
    scripts/migrate_encrypt_existing_data.py."""
    if not value:
        return value
    if not is_field_encryption_configured():
        return value
    try:
        return decrypt_field(value)
    except Exception:
        return value
