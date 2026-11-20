from datetime import date

import pytest
import pytest_asyncio

from database import Database, SlotTakenError
from services.booking_service import BookingService


@pytest_asyncio.fixture
async def service(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    await database.add_user(1, username="user1", first_name="Ivan")
    await database.add_user(2, username="user2", first_name="Petr")
    svc = BookingService(database)
    yield svc, database
    await database.close()


@pytest.mark.asyncio
async def test_available_slots_excludes_taken(service):
    svc, db = service
    target = date(2026, 9, 1)

    await db.create_booking(1, "interior", "2026-09-01", "10:00")
    await db.create_booking(2, "street", "2026-09-01", "14:00")

    slots = await svc.get_available_slots(target)
    assert "10:00" not in slots
    assert "14:00" not in slots
    assert "11:00" in slots

@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    await database.add_user(1, username="user1", first_name="Ivan")
    await database.add_user(2, username="user2", first_name="Petr")
    yield database
    await database.close()

@pytest.mark.asyncio
async def test_create_booking_via_service(service):
    svc, _ = service
    booking = await svc.create_booking(
        user_id=1,
        photosession_type="pets",
        target_date=date(2026, 9, 10),
        time="16:00",
    )
    assert booking.photosession_type == "pets"
    assert booking.time == "16:00"
    assert booking.status == "pending"


@pytest.mark.asyncio
async def test_format_booking_text(service):
    svc, db = service
    order = await db.create_booking(1, "interior", "2026-09-20", "18:00")
    booking = svc.order_to_booking(order)
    text = svc.format_booking_text(booking)
    assert "Интерьер" in text
    assert "18:00" in text

@pytest.mark.asyncio
async def test_format_booking_text_unknown_type(service):
    svc, db = service
    order = await db.create_booking(1, "unknown_type", "2026-09-20", "18:00")
    booking = svc.order_to_booking(order)
    text = svc.format_booking_text(booking)
    assert "Фотосессия" in text  # fallback


@pytest.mark.asyncio
async def test_create_booking_for_unknown_user_returns_none(db):
    # user_id=999 нет в users — должен вернуться None, а не SlotTakenError
    result = await db.create_booking(999, "studio", "2026-09-01", "10:00")
    assert result is None


@pytest.mark.asyncio
async def test_save_user_state_creates_user_if_missing(db):
    # user_id=777 нет в users
    ok = await db.save_user_state(777, menu="booking", type="studio")
    assert ok is True
    user = await db.get_user(777)
    assert user is not None  # строка создана

@pytest.mark.asyncio
async def test_revenue_stats_interior(db):
    await db.add_user(1, username="u1")
    order = await db.create_booking(1, "interior", "2026-09-01", "10:00")
    await db.update_order_status(order["order_id"], "confirmed")
    stats = await db.get_revenue_stats()
    assert stats["total"] > 0  # цена из PRICES["interior"]

@pytest.mark.asyncio
async def test_interior_and_studio_share_slot(db):
    await db.create_booking(1, "studio", "2026-09-01", "10:00")
    with pytest.raises(SlotTakenError):
        await db.create_booking(1, "interior", "2026-09-01", "10:00")

