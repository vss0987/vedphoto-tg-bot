import pytest
import pytest_asyncio

from database import Database, SlotTakenError, OrderNotFoundError


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    # Пользователи, от имени которых тесты создают брони
    await database.add_user(1, username="user1", first_name="Ivan")
    await database.add_user(2, username="user2", first_name="Petr")
    await database.add_user(5, username="quser", first_name="Q")
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_add_user_preserves_registered_at(db):
    await db.add_user(1, "user1", "Ivan", "Ivanov")
    first = await db.get_user(1)
    registered_at = first["registered_at"]

    await db.add_user(1, "user1_new", "Ivan", "Petrov")
    second = await db.get_user(1)

    assert second["username"] == "user1_new"
    assert second["registered_at"] == registered_at


@pytest.mark.asyncio
async def test_create_booking_and_slot_uniqueness(db):
    order = await db.create_booking(1, "studio", "2026-08-01", "10:00")
    assert order["order_id"] == 1
    assert order["status"] == "pending"

    with pytest.raises(SlotTakenError):
        await db.create_booking(2, "street", "2026-08-01", "10:00")


@pytest.mark.asyncio
async def test_cancelled_slot_can_be_rebooked(db):
    order = await db.create_booking(1, "studio", "2026-08-02", "11:00")
    await db.cancel_order_for_user(order["order_id"], 1)

    new_order = await db.create_booking(2, "street", "2026-08-02", "11:00")
    assert new_order["user_id"] == 2


@pytest.mark.asyncio
async def test_cancel_order_requires_ownership(db):
    order = await db.create_booking(1, "studio", "2026-08-03", "12:00")

    with pytest.raises(OrderNotFoundError):
        await db.cancel_order_for_user(order["order_id"], 999)

    assert await db.cancel_order_for_user(order["order_id"], 1)


@pytest.mark.asyncio
async def test_reschedule_checks_ownership_and_slots(db):
    order = await db.create_booking(1, "studio", "2026-08-04", "13:00")
    await db.create_booking(2, "street", "2026-08-05", "14:00")

    with pytest.raises(OrderNotFoundError):
        await db.reschedule_order_for_user(order["order_id"], 999, "2026-08-05", "15:00")

    assert await db.reschedule_order_for_user(order["order_id"], 1, "2026-08-05", "15:00")

    with pytest.raises(SlotTakenError):
        await db.reschedule_order_for_user(order["order_id"], 1, "2026-08-05", "14:00")


@pytest.mark.asyncio
async def test_save_question(db):
    await db.add_user(5, "quser", "Q", "User")
    qid = await db.save_question(5, "quser", "Когда можно записаться?")
    assert qid == 1


@pytest.mark.asyncio
async def test_get_user_booking_dates_in_month(db):
    await db.create_booking(1, "studio", "2026-08-07", "10:00")
    await db.create_booking(1, "studio", "2026-08-15", "11:00")

    days = await db.get_user_booking_dates_in_month(1, 2026, 8)
    assert days == {7, 15}
