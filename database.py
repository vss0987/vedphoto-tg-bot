import logging
import json
import os
from datetime import datetime, date, timedelta

import aiosqlite

import config
from crypto_utils import encrypt_field, decrypt_field_safe, encrypt_bytes

logger = logging.getLogger(__name__)


class SlotTakenError(Exception):
    """Слот уже занят другим пользователем."""


class OrderNotFoundError(Exception):
    """Заказ не найден или не принадлежит пользователю."""


class Database:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.DB_PATH

    async def connect(self):
        # На сервере папка под БД (например data/) обычно в .gitignore и не
        # приходит вместе с кодом — создаём её сами, если её ещё нет, иначе
        # aiosqlite падает с "unable to open database file".
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute("PRAGMA foreign_keys = ON;")
        await self.init_tables()
        return self.conn

    async def init_tables(self):
        # --- Таблица users (расширенная) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS users
                                (
                                    user_id
                                    INTEGER
                                    PRIMARY
                                    KEY,
                                    full_name
                                    TEXT,
                                    username
                                    TEXT,
                                    phone
                                    TEXT,
                                    registered_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP,
                                    is_blocked
                                    BOOLEAN
                                    DEFAULT
                                    FALSE,
                                    last_interaction
                                    TIMESTAMP,
                                    interaction_history
                                    TEXT
                                    DEFAULT
                                    '[]'
                                )
                                """)

        # --- Таблица orders (с full_name для истории) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS orders
                                (
                                    order_id
                                    INTEGER
                                    PRIMARY
                                    KEY
                                    AUTOINCREMENT,
                                    user_id
                                    INTEGER
                                    NOT
                                    NULL,
                                    full_name
                                    TEXT,
                                    photosession_type
                                    TEXT
                                    NOT
                                    NULL,
                                    status
                                    TEXT
                                    DEFAULT
                                    'pending',
                                    notes
                                    TEXT,
                                    booking_date
                                    TEXT,
                                    booking_time
                                    TEXT,
                                    created_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP,
                                    updated_at
                                    TIMESTAMP,
                                    FOREIGN
                                    KEY
                                (
                                    user_id
                                ) REFERENCES users
                                (
                                    user_id
                                ) ON DELETE CASCADE
                                    )
                                """)

        # --- Таблица user_states (оставляем) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS user_states
                                (
                                    user_id
                                    INTEGER
                                    PRIMARY
                                    KEY,
                                    current_menu
                                    TEXT,
                                    current_type
                                    TEXT,
                                    current_index
                                    INTEGER
                                    DEFAULT
                                    0,
                                    updated_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP,
                                    FOREIGN
                                    KEY
                                (
                                    user_id
                                ) REFERENCES users
                                (
                                    user_id
                                ) ON DELETE CASCADE
                                    )
                                """)

        # --- Таблица questions (оставляем) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS questions
                                (
                                    question_id
                                    INTEGER
                                    PRIMARY
                                    KEY
                                    AUTOINCREMENT,
                                    user_id
                                    INTEGER
                                    NOT
                                    NULL,
                                    username
                                    TEXT,
                                    text
                                    TEXT
                                    NOT
                                    NULL,
                                    created_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP,
                                    FOREIGN
                                    KEY
                                (
                                    user_id
                                ) REFERENCES users
                                (
                                    user_id
                                ) ON DELETE CASCADE
                                    )
                                """)

        # --- Таблица blocked_dates (админ закрывает даты: отпуск/больничный) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS blocked_dates
                                (
                                    booking_date
                                    TEXT
                                    PRIMARY
                                    KEY,
                                    reason
                                    TEXT,
                                    created_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP
                                )
                                """)

        # --- Таблица interaction_log: полный лог взаимодействий пользователя
        # с ботом (для еженедельной выгрузки в CSV-архив) ---
        await self.conn.execute("""
                                CREATE TABLE IF NOT EXISTS interaction_log
                                (
                                    log_id
                                    INTEGER
                                    PRIMARY
                                    KEY
                                    AUTOINCREMENT,
                                    user_id
                                    INTEGER
                                    NOT
                                    NULL,
                                    full_name
                                    TEXT,
                                    username
                                    TEXT,
                                    interaction_type
                                    TEXT
                                    NOT
                                    NULL,
                                    section
                                    TEXT,
                                    details
                                    TEXT,
                                    created_at
                                    TIMESTAMP
                                    DEFAULT
                                    CURRENT_TIMESTAMP
                                )
                                """)
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_interaction_log_created_at "
            "ON interaction_log (created_at)"
        )

        # --- Миграции для существующих таблиц ---
        cursor = await self.conn.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in await cursor.fetchall()}

        if "phone" not in user_columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        if "is_blocked" not in user_columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE")
        if "last_interaction" not in user_columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN last_interaction TIMESTAMP")
        if "interaction_history" not in user_columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN interaction_history TEXT DEFAULT '[]'")
        if "full_name" not in user_columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT")

        cursor = await self.conn.execute("PRAGMA table_info(orders)")
        order_columns = {row[1] for row in await cursor.fetchall()}

        if "full_name" not in order_columns:
            await self.conn.execute("ALTER TABLE orders ADD COLUMN full_name TEXT")
        if "updated_at" not in order_columns:
            await self.conn.execute("ALTER TABLE orders ADD COLUMN updated_at TIMESTAMP")
        if "booking_date" not in order_columns:
            await self.conn.execute("ALTER TABLE orders ADD COLUMN booking_date TEXT")
        if "booking_time" not in order_columns:
            await self.conn.execute("ALTER TABLE orders ADD COLUMN booking_time TEXT")

        cursor = await self.conn.execute("PRAGMA table_info(interaction_log)")
        interaction_log_columns = {row[1] for row in await cursor.fetchall()}

        if "encrypted_payload" not in interaction_log_columns:
            # full_name/username/section/details теперь пишутся сюда одним
            # зашифрованным блоком (см. log_interaction) вместо отдельных
            # текстовых колонок — старые колонки остаются в схеме ради
            # совместимости со строками, записанными до этой миграции
            # (см. export_interactions_rows), но больше не заполняются.
            await self.conn.execute("ALTER TABLE interaction_log ADD COLUMN encrypted_payload TEXT")

        # --- Индекс для уникальных слотов ---
        await self.conn.execute("""
                                CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_slot
                                    ON orders (booking_date, booking_time)
                                    WHERE status != 'cancelled'
                                    AND booking_date IS NOT NULL
                                    AND booking_time IS NOT NULL
                                """)

        await self.conn.commit()
        logger.info("Database tables ready")

    # --- Вспомогательные методы ---
    @staticmethod
    def _row_to_user(row):
        return {
            "user_id": row[0],
            "full_name": decrypt_field_safe(row[1]),
            "username": decrypt_field_safe(row[2]),
            "phone": decrypt_field_safe(row[3]),
            "registered_at": row[4],
            "is_blocked": bool(row[5]),
            "last_interaction": row[6],
            "interaction_history": row[7],
        }

    @staticmethod
    def _row_to_order(row):
        return {
            "order_id": row[0],
            "user_id": row[1],
            "full_name": decrypt_field_safe(row[2]),
            "photosession_type": row[3],
            "status": row[4],
            "notes": decrypt_field_safe(row[5]),
            "booking_date": row[6],
            "booking_time": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    @staticmethod
    def _row_to_order_with_contact(row):
        """Как _row_to_order, но с добавленными username/phone из таблицы users
        (когда строка получена через LEFT JOIN users)."""
        order = Database._row_to_order(row[:10])
        order["username"] = decrypt_field_safe(row[10]) if len(row) > 10 else None
        order["phone"] = decrypt_field_safe(row[11]) if len(row) > 11 else None
        return order

    # --- Методы для users ---
    async def add_user(
            self,
            user_id: int,
            username: str | None = None,
            first_name: str | None = None,
            last_name: str | None = None,
            phone: str | None = None,
    ) -> bool:
        """Добавляет или обновляет пользователя."""
        full_name = f"{first_name or ''} {last_name or ''}".strip()
        enc_full_name = encrypt_field(full_name) if full_name else None
        enc_username = encrypt_field(username)
        enc_phone = encrypt_field(phone)
        try:
            await self.conn.execute(
                """
                INSERT INTO users (user_id, full_name, username, phone, last_interaction)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO
                UPDATE SET
                    full_name = COALESCE (?, full_name),
                    username = COALESCE (?, username),
                    phone = COALESCE (?, phone),
                    last_interaction = CURRENT_TIMESTAMP
                """,
                (user_id, enc_full_name, enc_username, enc_phone,
                 enc_full_name, enc_username, enc_phone),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error adding user %s", user_id)
            return False

    async def get_user(self, user_id: int):
        try:
            cursor = await self.conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return self._row_to_user(row) if row else None
        except Exception:
            logger.exception("Error getting user %s", user_id)
            return None

    async def update_phone(self, user_id: int, phone: str) -> bool:
        """Обновляет номер телефона пользователя."""
        try:
            await self.conn.execute(
                "UPDATE users SET phone = ?, last_interaction = CURRENT_TIMESTAMP WHERE user_id = ?",
                (encrypt_field(phone), user_id),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error updating phone for user %s", user_id)
            return False

    async def update_block_status(self, user_id: int, is_blocked: bool) -> bool:
        """Обновляет статус блокировки бота."""
        try:
            await self.conn.execute(
                "UPDATE users SET is_blocked = ? WHERE user_id = ?",
                (is_blocked, user_id),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error updating block status for user %s", user_id)
            return False

    async def add_interaction(self, user_id: int, action: str) -> bool:
        """Добавляет запись в историю взаимодействий (зашифрованно — сюда
        попадают и телефон, и фрагменты вопросов, см. места вызова)."""
        try:
            user_row = await self.conn.execute(
                "SELECT interaction_history FROM users WHERE user_id = ?", (user_id,)
            )
            row = await user_row.fetchone()
            if not row:
                return False

            raw_history = decrypt_field_safe(row[0]) or "[]"
            history = json.loads(raw_history)
            history.append({
                "timestamp": datetime.now().isoformat(),
                "action": action,
            })

            # Ограничиваем историю 100 последними записями
            if len(history) > 100:
                history = history[-100:]

            await self.conn.execute(
                """
                UPDATE users
                SET interaction_history = ?,
                    last_interaction    = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (encrypt_field(json.dumps(history, ensure_ascii=False)), user_id),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error adding interaction for user %s", user_id)
            return False

    async def get_interaction_history(self, user_id: int, limit: int = 10) -> list:
        """Возвращает последние N записей истории."""
        try:
            cursor = await self.conn.execute(
                "SELECT interaction_history FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return []
            raw_history = decrypt_field_safe(row[0]) or "[]"
            history = json.loads(raw_history)
            return history[-limit:] if history else []
        except Exception:
            logger.exception("Error getting history for user %s", user_id)
            return []

    # --- Методы для orders ---
    async def create_booking(
            self,
            user_id: int,
            photosession_type: str,
            booking_date: str,
            booking_time: str,
            notes: str = "",
    ) -> dict | None:
        """Атомарное создание брони с проверкой уникальности слота."""
        try:
            # Получаем пользователя для full_name (уже расшифрованное значение —
            # get_user идёт через _row_to_user, который сам расшифровывает)
            user = await self.get_user(user_id)
            full_name = user["full_name"] if user else ""

            cursor = await self.conn.execute(
                """
                INSERT INTO orders (user_id, full_name, photosession_type, notes,
                                    booking_date, booking_time, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
                """,
                (user_id, encrypt_field(full_name), photosession_type, encrypt_field(notes),
                 booking_date, booking_time),
            )
            await self.conn.commit()
            order_id = cursor.lastrowid

            # Добавляем в историю
            await self.add_interaction(
                user_id,
                f"Создан заказ #{order_id} ({photosession_type}) на {booking_date} {booking_time}"
            )

            return {
                "order_id": order_id,
                "user_id": user_id,
                "full_name": full_name,
                "photosession_type": photosession_type,
                "notes": notes,
                "status": "pending",
                "booking_date": booking_date,
                "booking_time": booking_time,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        except aiosqlite.IntegrityError as exc:
            if "FOREIGN KEY" in str(exc):
                logger.error("create_booking: user %s is not registered", user_id)
                return None
            logger.warning("Slot taken: %s %s — %s", booking_date, booking_time, exc)
            raise SlotTakenError from exc
        except Exception:
            logger.exception("Error creating booking for user %s", user_id)
            return None

    async def get_user_orders(self, user_id: int, exclude_cancelled: bool = False):
        try:
            query = """
                    SELECT order_id, \
                           user_id, \
                           full_name, \
                           photosession_type, \
                           status, \
                           notes,
                           booking_date, \
                           booking_time, \
                           created_at, \
                           updated_at
                    FROM orders \
                    WHERE user_id = ? \
                    """
            params = [user_id]
            if exclude_cancelled:
                query += " AND status != 'cancelled'"
            query += " ORDER BY created_at DESC"
            cursor = await self.conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_order(row) for row in rows]
        except Exception:
            logger.exception("Error getting orders for user %s", user_id)
            return []

    async def get_order(self, order_id: int):
        try:
            cursor = await self.conn.execute(
                """
                SELECT order_id,
                       user_id,
                       full_name,
                       photosession_type,
                       status,
                       notes,
                       booking_date,
                       booking_time,
                       created_at,
                       updated_at
                FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_order(row) if row else None
        except Exception:
            logger.exception("Error getting order %s", order_id)
            return None

    async def get_order_for_user(self, order_id: int, user_id: int):
        order = await self.get_order(order_id)
        if not order or order["user_id"] != user_id:
            return None
        return order

    async def get_orders_by_date(self, booking_date: str, exclude_status: str = "cancelled"):
        try:
            cursor = await self.conn.execute(
                """
                SELECT o.order_id,
                       o.user_id,
                       o.full_name,
                       o.photosession_type,
                       o.status,
                       o.notes,
                       o.booking_date,
                       o.booking_time,
                       o.created_at,
                       o.updated_at,
                       u.username,
                       u.phone
                FROM orders o
                LEFT JOIN users u ON u.user_id = o.user_id
                WHERE o.booking_date = ?
                  AND o.status != ?
                """,
                (booking_date, exclude_status),
            )
            rows = await cursor.fetchall()
            return [self._row_to_order_with_contact(row) for row in rows]
        except Exception:
            logger.exception("Error getting orders by date %s", booking_date)
            return []

    async def get_user_booking_dates_in_month(self, user_id: int, year: int, month: int) -> set[int]:
        """Дни месяца, на которые у пользователя есть активные брони."""
        prefix = f"{year:04d}-{month:02d}-"
        try:
            cursor = await self.conn.execute(
                """
                SELECT booking_date
                FROM orders
                WHERE user_id = ?
                  AND status != 'cancelled'
                  AND booking_date LIKE ?
                """,
                (user_id, f"{prefix}%"),
            )
            rows = await cursor.fetchall()
            days = set()
            for (booking_date,) in rows:
                if booking_date:
                    days.add(date.fromisoformat(booking_date).day)
            return days
        except Exception:
            logger.exception("Error getting booking dates for user %s", user_id)
            return set()

    async def get_all_booking_days_in_month(self, year: int, month: int) -> set[int]:
        """Дни месяца с активными бронями ЛЮБОГО пользователя (для календаря админа)."""
        prefix = f"{year:04d}-{month:02d}-"
        try:
            cursor = await self.conn.execute(
                """
                SELECT booking_date
                FROM orders
                WHERE status != 'cancelled'
                  AND booking_date LIKE ?
                """,
                (f"{prefix}%",),
            )
            rows = await cursor.fetchall()
            days = set()
            for (booking_date,) in rows:
                if booking_date:
                    days.add(date.fromisoformat(booking_date).day)
            return days
        except Exception:
            logger.exception("Error getting all booking days for %s-%s", year, month)
            return set()

    # --- Методы для blocked_dates (админ закрывает даты: отпуск/больничный) ---
    async def block_dates(self, dates: list[str], reason: str = "") -> bool:
        """Закрывает список дат (формат YYYY-MM-DD) для новых записей."""
        try:
            await self.conn.executemany(
                """
                INSERT INTO blocked_dates (booking_date, reason)
                VALUES (?, ?)
                ON CONFLICT(booking_date) DO UPDATE SET reason = excluded.reason
                """,
                [(d, reason) for d in dates],
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error blocking dates: %s", dates)
            return False

    async def unblock_date(self, booking_date: str) -> bool:
        try:
            await self.conn.execute(
                "DELETE FROM blocked_dates WHERE booking_date = ?", (booking_date,)
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error unblocking date %s", booking_date)
            return False

    async def unblock_date_range(self, start_date: str, end_date: str) -> bool:
        try:
            await self.conn.execute(
                "DELETE FROM blocked_dates WHERE booking_date >= ? AND booking_date <= ?",
                (start_date, end_date),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error unblocking range %s..%s", start_date, end_date)
            return False

    async def is_date_blocked(self, booking_date: str) -> bool:
        try:
            cursor = await self.conn.execute(
                "SELECT 1 FROM blocked_dates WHERE booking_date = ?", (booking_date,)
            )
            return (await cursor.fetchone()) is not None
        except Exception:
            logger.exception("Error checking blocked date %s", booking_date)
            return False

    async def get_blocked_days_in_month(self, year: int, month: int) -> set[int]:
        prefix = f"{year:04d}-{month:02d}-"
        try:
            cursor = await self.conn.execute(
                "SELECT booking_date FROM blocked_dates WHERE booking_date LIKE ?",
                (f"{prefix}%",),
            )
            rows = await cursor.fetchall()
            days = set()
            for (booking_date,) in rows:
                days.add(date.fromisoformat(booking_date).day)
            return days
        except Exception:
            logger.exception("Error getting blocked days for %s-%s", year, month)
            return set()

    async def get_all_blocked_dates(self, upcoming_only: bool = True, limit: int = 200) -> list[dict]:
        """Все закрытые даты (для списка в админке), по возрастанию.
        upcoming_only=True — не показывать уже прошедшие закрытые даты."""
        try:
            if upcoming_only:
                cursor = await self.conn.execute(
                    "SELECT booking_date, reason FROM blocked_dates "
                    "WHERE booking_date >= ? ORDER BY booking_date ASC LIMIT ?",
                    (date.today().isoformat(), limit),
                )
            else:
                cursor = await self.conn.execute(
                    "SELECT booking_date, reason FROM blocked_dates "
                    "ORDER BY booking_date ASC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [{"booking_date": r[0], "reason": r[1]} for r in rows]
        except Exception:
            logger.exception("Error getting all blocked dates")
            return []

    async def cancel_order(self, order_id: int) -> bool:
        """Отменяет заказ БЕЗ проверки владельца (для админа — например,
        когда нужно отменить чужую бронь при закрытии дат на отпуск)."""
        try:
            await self.conn.execute(
                """
                UPDATE orders
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (order_id,),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error (admin) cancelling order %s", order_id)
            return False

    async def cancel_order_for_user(self, order_id: int, user_id: int) -> bool:
        order = await self.get_order_for_user(order_id, user_id)
        if not order:
            raise OrderNotFoundError
        if order["status"] == "cancelled":
            return True
        try:
            await self.conn.execute(
                """
                UPDATE orders
                SET status     = 'cancelled',
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                  AND user_id = ?
                """,
                (order_id, user_id),
            )
            await self.conn.commit()

            await self.add_interaction(user_id, f"Отменён заказ #{order_id}")
            return True
        except Exception:
            logger.exception("Error cancelling order %s", order_id)
            return False

    async def reschedule_order_for_user(
            self, order_id: int, user_id: int, booking_date: str, booking_time: str
    ) -> bool:
        order = await self.get_order_for_user(order_id, user_id)
        if not order:
            raise OrderNotFoundError
        if order["status"] == "cancelled":
            return False
        try:
            await self.conn.execute(
                """
                UPDATE orders
                SET booking_date = ?,
                    booking_time = ?,
                    status       = 'pending',
                    updated_at   = CURRENT_TIMESTAMP
                WHERE order_id = ?
                  AND user_id = ?
                """,
                (booking_date, booking_time, order_id, user_id),
            )
            await self.conn.commit()

            await self.add_interaction(
                user_id,
                f"Перенесён заказ #{order_id} на {booking_date} {booking_time}"
            )
            return True
        except aiosqlite.IntegrityError as exc:
            logger.warning("Reschedule slot taken: %s %s — %s", booking_date, booking_time, exc)
            raise SlotTakenError from exc
        except Exception:
            logger.exception("Error rescheduling order %s", order_id)
            return False

    async def update_order_status(self, order_id: int, new_status: str) -> bool:
        """Обновляет статус заказа (для админа)."""
        try:
            await self.conn.execute(
                """
                UPDATE orders
                SET status     = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (new_status, order_id),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error updating order status %s", order_id)
            return False

    # --- Методы для user_states (оставляем без изменений) ---
    async def save_user_state(self, user_id: int, menu: str, type: str = "", index: int = 0):
        try:
            # Гарантируем наличие строки в users — иначе FOREIGN KEY на
            # user_states падает, если пришёл callback из старого сообщения
            # (до /start или после пересоздания БД).
            await self.conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO user_states
                    (user_id, current_menu, current_type, current_index)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, menu, type, index),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error saving state for user %s", user_id)
            return False

    async def get_user_state(self, user_id: int):
        try:
            cursor = await self.conn.execute(
                "SELECT * FROM user_states WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "current_menu": row[1],
                    "current_type": row[2],
                    "current_index": row[3],
                    "updated_at": row[4],
                }
            return None
        except Exception:
            logger.exception("Error getting state for user %s", user_id)
            return None

    async def clear_user_state(self, user_id: int):
        try:
            await self.conn.execute(
                "DELETE FROM user_states WHERE user_id = ?", (user_id,)
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error clearing state for user %s", user_id)
            return False

    # --- Методы для questions (оставляем) ---
    async def save_question(self, user_id: int, username: str, text: str) -> int | None:
        try:
            cursor = await self.conn.execute(
                """
                INSERT INTO questions (user_id, username, text)
                VALUES (?, ?, ?)
                """,
                (user_id, encrypt_field(username), encrypt_field(text)),
            )
            await self.conn.commit()

            await self.add_interaction(user_id, f"Задан вопрос: {text[:50]}...")
            return cursor.lastrowid
        except Exception:
            logger.exception("Error saving question from user %s", user_id)
            return None

    async def close(self):
        if hasattr(self, "conn"):
            await self.conn.close()

    async def get_user_phone(self, user_id: int) -> str | None:
        """Возвращает номер телефона пользователя."""
        try:
            cursor = await self.conn.execute(
                "SELECT phone FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return decrypt_field_safe(row[0]) if row else None
        except Exception:
            logger.exception("Error getting phone for user %s", user_id)
            return None

    async def delete_user_phone(self, user_id: int) -> bool:
        """Удаляет сохранённый номер телефона пользователя (по его запросу
        на удаление персональных данных — см. дисклеймер в
        phone_required.py). Сами записи/брони не трогает, только номер."""
        try:
            await self.conn.execute(
                "UPDATE users SET phone = NULL, last_interaction = CURRENT_TIMESTAMP "
                "WHERE user_id = ?",
                (user_id,),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error deleting phone for user %s", user_id)
            return False

    # ============ АДМИН-МЕТОДЫ ============


    async def get_all_orders(self, limit: int = 100) -> list:
        """Получить все заказы вместе с телефоном и telegram-username пользователя."""
        try:
            cursor = await self.conn.execute(
                """
                SELECT o.order_id, o.user_id, o.full_name, o.photosession_type, o.status,
                       o.notes, o.booking_date, o.booking_time, o.created_at, o.updated_at,
                       u.username, u.phone
                FROM orders o
                LEFT JOIN users u ON u.user_id = o.user_id
                ORDER BY o.created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_order_with_contact(row) for row in rows]
        except Exception:
            logger.exception("Error getting all orders")
            return []

    async def get_orders_by_status(self, status: str) -> list:
        """Получить заказы по статусу вместе с телефоном и telegram-username пользователя."""
        try:
            cursor = await self.conn.execute(
                """
                SELECT o.order_id, o.user_id, o.full_name, o.photosession_type, o.status,
                       o.notes, o.booking_date, o.booking_time, o.created_at, o.updated_at,
                       u.username, u.phone
                FROM orders o
                LEFT JOIN users u ON u.user_id = o.user_id
                WHERE o.status = ?
                ORDER BY o.created_at DESC
                """,
                (status,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_order_with_contact(row) for row in rows]
        except Exception:
            logger.exception("Error getting orders by status %s", status)
            return []


    async def get_orders_count(self, status: str = None) -> int:
        """Количество заказов (всех или по статусу)"""
        try:
            if status:
                cursor = await self.conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE status = ?",
                    (status,)
                )
            else:
                cursor = await self.conn.execute("SELECT COUNT(*) FROM orders")
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            logger.exception("Error counting orders (status=%s)", status)
            return 0


    async def get_users_count(self) -> int:
        """Количество пользователей"""
        try:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM users")
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            logger.exception("Error counting users")
            return 0


    async def get_questions_count(self) -> int:
        """Количество вопросов"""
        try:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM questions")
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            logger.exception("Error counting questions")
            return 0


    async def get_all_questions(self, limit: int = 50) -> list:
        """Получить все вопросы вместе с контактными данными автора."""
        try:
            cursor = await self.conn.execute(
                """
                SELECT q.question_id, q.user_id, q.username, q.text, q.created_at,
                       u.full_name, u.username, u.phone
                FROM questions q
                LEFT JOIN users u ON u.user_id = q.user_id
                ORDER BY q.created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    # username, сохранённый в момент вопроса (для старых записей
                    # это могло быть "@Имя"-заглушка, если у юзера не было username)
                    "username": decrypt_field_safe(r[2]),
                    "text": decrypt_field_safe(r[3]),
                    "created_at": r[4],
                    "full_name": decrypt_field_safe(r[5]),
                    # актуальный telegram-username из таблицы users (может отличаться
                    # от того, что был сохранён в questions.username на момент вопроса)
                    "telegram_username": decrypt_field_safe(r[6]),
                    "phone": decrypt_field_safe(r[7]),
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Error getting all questions")
            return []


    async def get_all_users(self, limit: int = 50) -> list:
        """Получить всех пользователей"""
        try:
            cursor = await self.conn.execute(
                "SELECT * FROM users ORDER BY registered_at DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "full_name": decrypt_field_safe(r[1]),
                    "username": decrypt_field_safe(r[2]),
                    "phone": decrypt_field_safe(r[3]),
                    "registered_at": r[4],
                    "is_blocked": bool(r[5]) if r[5] is not None else False,
                    "last_interaction": r[6]
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Error getting all users")
            return []


    async def get_revenue_stats(self) -> dict:
        """
        Статистика по выручке.

        Цены в БД не хранятся — берутся из constants.PRICES по типу
        фотосессии (единый источник, тот же что и в разделе "Стоимость").
        В выручку идут заказы со статусом 'confirmed' или 'completed'
        (то есть уже подтверждённые/выполненные — 'pending' и 'cancelled'
        не считаются, т.к. по ним ещё не факт, что деньги будут получены).
        Периоды считаются по дате самой фотосессии (booking_date), а не
        по дате создания заказа.
        """
        from constants import PRICES

        try:
            cursor = await self.conn.execute(
                """
                SELECT photosession_type, booking_date
                FROM orders
                WHERE status IN ('confirmed', 'completed')
                  AND booking_date IS NOT NULL
                """
            )
            rows = await cursor.fetchall()
        except Exception:
            logger.exception("Error computing revenue stats")
            return {"today": 0, "week": 0, "month": 0, "total": 0}

        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # понедельник текущей недели
        month_start = today.replace(day=1)

        stats = {"today": 0, "week": 0, "month": 0, "total": 0}
        for photosession_type, booking_date_str in rows:
            price = PRICES.get(photosession_type, 0)
            stats["total"] += price
            try:
                booking_date = date.fromisoformat(booking_date_str)
            except (TypeError, ValueError):
                continue
            if booking_date == today:
                stats["today"] += price
            if booking_date >= week_start:
                stats["week"] += price
            if booking_date >= month_start:
                stats["month"] += price

        return stats


    # --- Методы для interaction_log (полный лог взаимодействий + CSV-архив) ---

    async def log_interaction(
        self,
        user_id: int,
        full_name: str | None,
        username: str | None,
        interaction_type: str,
        section: str | None,
        details: str | None = None,
    ) -> bool:
        """
        Записывает взаимодействие. Чувствительная часть (имя, username,
        раздел, детали) шифруется публичным ключом ПРЯМО ЗДЕСЬ, в момент
        записи — сервер после этого прочитать её уже не может (нет
        приватного ключа), только структурные поля (кто/когда/какой тип)
        остаются в открытом виде для базовых операций (счёт, еженедельная
        очистка). См. crypto_utils.py.
        """
        payload = json.dumps({
            "full_name": full_name,
            "username": username,
            "section": section,
            "details": details,
        }, ensure_ascii=False)

        encrypted_payload = None
        try:
            encrypted_payload = encrypt_bytes(payload.encode()).decode()
        except Exception:
            logger.exception(
                "Failed to encrypt interaction payload for user %s — payload not "
                "stored for this row (public_key.pem missing/misconfigured?)",
                user_id,
            )

        try:
            await self.conn.execute(
                """
                INSERT INTO interaction_log (user_id, interaction_type, encrypted_payload)
                VALUES (?, ?, ?)
                """,
                (user_id, interaction_type, encrypted_payload),
            )
            await self.conn.commit()
            return True
        except Exception:
            logger.exception("Error logging interaction for user %s", user_id)
            return False

    async def get_interaction_log_count(self) -> int:
        try:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM interaction_log")
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            logger.exception("Error counting interaction log")
            return 0

    async def export_interactions_rows(self) -> list[tuple]:
        """
        Возвращает строки interaction_log: (log_id, user_id, interaction_type,
        encrypted_payload, created_at). encrypted_payload уже зашифрован
        публичным ключом (см. log_interaction) — экспорт ничего не
        расшифровывает и не может.

        Легаси-строки, записанные ДО перехода на encrypted_payload (в
        старых текстовых колонках full_name/username/section/details),
        шифруются прямо здесь, на лету, перед экспортом — чтобы в
        итоговом файле не осталось вообще ни одной читаемой строки,
        независимо от того, когда она была записана.
        """
        try:
            cursor = await self.conn.execute(
                """
                SELECT log_id, user_id, interaction_type, encrypted_payload,
                       full_name, username, section, details, created_at
                FROM interaction_log
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
        except Exception:
            logger.exception("Error reading interaction log for export")
            return []

        result = []
        for (log_id, user_id, interaction_type, encrypted_payload,
             full_name, username, section, details, created_at) in rows:
            if not encrypted_payload:
                legacy_payload = json.dumps({
                    "full_name": full_name,
                    "username": username,
                    "section": section,
                    "details": details,
                }, ensure_ascii=False)
                try:
                    encrypted_payload = encrypt_bytes(legacy_payload.encode()).decode()
                except Exception:
                    logger.exception(
                        "Failed to encrypt legacy interaction_log row %s at export time", log_id
                    )
                    encrypted_payload = ""

            result.append((log_id, user_id, interaction_type, encrypted_payload, created_at))

        return result

    async def clear_interaction_log(self, before: str | None = None) -> int:
        """Удаляет записи из interaction_log (по умолчанию — все). Возвращает
        количество удалённых строк."""
        try:
            if before:
                cursor = await self.conn.execute(
                    "SELECT COUNT(*) FROM interaction_log WHERE created_at < ?", (before,)
                )
            else:
                cursor = await self.conn.execute("SELECT COUNT(*) FROM interaction_log")
            row = await cursor.fetchone()
            count = row[0] if row else 0

            if before:
                await self.conn.execute(
                    "DELETE FROM interaction_log WHERE created_at < ?", (before,)
                )
            else:
                await self.conn.execute("DELETE FROM interaction_log")
            await self.conn.commit()
            return count
        except Exception:
            logger.exception("Error clearing interaction log")
            return 0


db = Database()