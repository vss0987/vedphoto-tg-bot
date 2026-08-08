from datetime import date

import pytest
import pytest_asyncio

from database import Database
from services.booking_service import BookingService


@pytest_asyncio.fixture
async def service(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    svc = BookingService(database)
    yield svc, database
    await database.close()


@pytest.mark.asyncio
async def test_available_slots_excludes_taken(service):
    svc, db = service
    target = date(2026, 9, 1)

    await db.create_booking(1, "studio", "2026-09-01", "10:00")
    await db.create_booking(2, "street", "2026-09-01", "14:00")

    slots = await svc.get_available_slots(target)
    assert "10:00" not in slots
    assert "14:00" not in slots
    assert "11:00" in slots


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
    order = await db.create_booking(1, "celebration", "2026-09-20", "18:00", notes="Свадьба")
    booking = svc.order_to_booking(order)
    text = svc.format_booking_text(booking)
    assert "Торжества" in text
    assert "18:00" in text
