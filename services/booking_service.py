from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from constants import ACTIVE_STATUSES, TIME_SLOTS, TYPE_NAMES, STATUS_EMOJI
from database import Database, OrderNotFoundError, SlotTakenError, db


@dataclass
class Booking:
    order_id: int
    user_id: int
    photosession_type: str
    date: Optional[date]
    time: Optional[str]
    status: str
    notes: str = ""


class BookingService:
    def __init__(self, database: Database):
        self.db = database

    @staticmethod
    def date_to_str(d: date) -> str:
        return d.strftime("%Y-%m-%d")

    @staticmethod
    def str_to_date(s: str) -> date:
        return datetime.strptime(s, "%Y-%m-%d").date()

    @staticmethod
    def parse_date_callback(callback_data: str) -> date:
        _, year, month, day = callback_data.split("_")
        return date(int(year), int(month), int(day))

    def order_to_booking(self, order: dict) -> Booking:
        booking_date = (
            self.str_to_date(order["booking_date"]) if order.get("booking_date") else None
        )
        return Booking(
            order_id=order["order_id"],
            user_id=order["user_id"],
            photosession_type=order["photosession_type"],
            date=booking_date,
            time=order.get("booking_time"),
            status=order.get("status", "pending"),
            notes=order.get("notes") or "",
        )

    async def get_user_bookings(
        self, user_id: int, active_only: bool = False
    ) -> list[Booking]:
        orders = await self.db.get_user_orders(user_id, exclude_cancelled=active_only)
        bookings = [self.order_to_booking(o) for o in orders]
        if active_only:
            bookings = [b for b in bookings if b.status in ACTIVE_STATUSES]
        return bookings

    async def get_bookings_by_date(self, user_id: int, target_date: date) -> list[Booking]:
        return [
            b
            for b in await self.get_user_bookings(user_id)
            if b.date == target_date and b.status != "cancelled"
        ]

    async def get_available_slots(self, target_date: date, exclude_order_id: int | None = None) -> list[str]:
        taken_orders = await self.db.get_orders_by_date(self.date_to_str(target_date))
        taken_times = {
            o["booking_time"]
            for o in taken_orders
            if o.get("booking_time") and o["order_id"] != exclude_order_id
        }
        return [slot for slot in TIME_SLOTS if slot not in taken_times]

    async def get_user_booking_days(self, user_id: int, year: int, month: int) -> set[int]:
        return await self.db.get_user_booking_dates_in_month(user_id, year, month)

    async def create_booking(
        self,
        user_id: int,
        photosession_type: str,
        target_date: date,
        time: str,
        notes: str = "",
    ) -> Booking:
        order = await self.db.create_booking(
            user_id=user_id,
            photosession_type=photosession_type,
            booking_date=self.date_to_str(target_date),
            booking_time=time,
            notes=notes or "Забронировано через бота",
        )
        if not order:
            raise RuntimeError("Не удалось создать бронь")
        return self.order_to_booking(order)

    async def cancel_booking(self, order_id: int, user_id: int) -> bool:
        return await self.db.cancel_order_for_user(order_id, user_id)

    async def reschedule_booking(
        self, order_id: int, user_id: int, target_date: date, time: str
    ) -> bool:
        return await self.db.reschedule_order_for_user(
            order_id,
            user_id,
            self.date_to_str(target_date),
            time,
        )

    def format_booking_text(self, booking: Booking) -> str:
        type_name = TYPE_NAMES.get(booking.photosession_type, "Фотосессия")
        emoji = STATUS_EMOJI.get(booking.status, "📌")
        date_str = booking.date.strftime("%d.%m.%Y") if booking.date else "—"
        return (
            f"{emoji} <b>{type_name}</b>\n"
            f"📅 {date_str}\n"
            f"⏰ {booking.time or '—'}\n"
            f"📝 {booking.notes or 'Без примечаний'}"
        )

    @staticmethod
    def calendar_view_text() -> str:
        return (
            "📅 <b>Календарь бронирований</b>\n\n"
            "<i>Нажми на день, чтобы посмотреть или создать бронь</i>"
        )

    @staticmethod
    def get_current_month() -> tuple[int, int]:
        today = date.today()
        return today.year, today.month


booking_service = BookingService(db)

__all__ = [
    "Booking",
    "BookingService",
    "OrderNotFoundError",
    "SlotTakenError",
    "booking_service",
]
