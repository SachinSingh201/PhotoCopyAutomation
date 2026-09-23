import pytest
from backend.core.exceptions import AppException, ErrorCode
from backend.models.order import Order
from backend.models.user import User
from backend.services.orders.state_machine import OrderState, transition_order


@pytest.mark.asyncio
async def test_legal_state_transitions(db_session):
    # Setup test user and order
    user = User(whatsapp_user_id="wa_123", phone_number="+919999999999")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-1001",
        user_id=user.id,
        status=OrderState.CREATED.value,
    )
    db_session.add(order)
    await db_session.flush()

    # Step 1: CREATED -> FILES_UPLOADING
    await transition_order(db_session, order, OrderState.FILES_UPLOADING, "CUSTOMER")
    assert order.status == OrderState.FILES_UPLOADING.value

    # Step 2: FILES_UPLOADING -> FILES_UPLOADED
    await transition_order(db_session, order, OrderState.FILES_UPLOADED, "CUSTOMER")
    assert order.status == OrderState.FILES_UPLOADED.value

    # Step 3: FILES_UPLOADED -> CONFIGURATION_PENDING
    await transition_order(db_session, order, OrderState.CONFIGURATION_PENDING, "CUSTOMER")
    assert order.status == OrderState.CONFIGURATION_PENDING.value

    # Step 4: CONFIGURATION_PENDING -> CONFIGURED
    await transition_order(db_session, order, OrderState.CONFIGURED, "CUSTOMER")
    assert order.status == OrderState.CONFIGURED.value

    # Step 5: CONFIGURED -> PRICE_CALCULATED
    await transition_order(db_session, order, OrderState.PRICE_CALCULATED, "BACKEND")
    assert order.status == OrderState.PRICE_CALCULATED.value

    # Step 6: PRICE_CALCULATED -> AWAITING_CONFIRMATION
    await transition_order(db_session, order, OrderState.AWAITING_CONFIRMATION, "BACKEND")
    assert order.status == OrderState.AWAITING_CONFIRMATION.value

    # Step 7: AWAITING_CONFIRMATION -> PAYMENT_PENDING
    await transition_order(db_session, order, OrderState.PAYMENT_PENDING, "CUSTOMER")
    assert order.status == OrderState.PAYMENT_PENDING.value

    # Step 8: PAYMENT_PENDING -> PAID
    await transition_order(db_session, order, OrderState.PAID, "PAYMENT_GATEWAY")
    assert order.status == OrderState.PAID.value
    assert order.paid_at is not None

    # Step 9: PAID -> QUEUED
    await transition_order(db_session, order, OrderState.QUEUED, "BACKEND")
    assert order.status == OrderState.QUEUED.value

    # Step 10: QUEUED -> PRINTING
    await transition_order(db_session, order, OrderState.PRINTING, "AGENT")
    assert order.status == OrderState.PRINTING.value

    # Step 11: PRINTING -> PRINTED
    await transition_order(db_session, order, OrderState.PRINTED, "AGENT")
    assert order.status == OrderState.PRINTED.value
    assert order.printed_at is not None

    # Step 12: PRINTED -> READY_FOR_PICKUP
    await transition_order(db_session, order, OrderState.READY_FOR_PICKUP, "BACKEND")
    assert order.status == OrderState.READY_FOR_PICKUP.value

    # Step 13: READY_FOR_PICKUP -> COLLECTED
    await transition_order(db_session, order, OrderState.COLLECTED, "ADMIN")
    assert order.status == OrderState.COLLECTED.value
    assert order.collected_at is not None

    # Step 14: COLLECTED -> CLEANUP_PENDING -> CLEANED
    await transition_order(db_session, order, OrderState.CLEANUP_PENDING, "BACKEND")
    await transition_order(db_session, order, OrderState.CLEANED, "WORKER")
    assert order.status == OrderState.CLEANED.value


@pytest.mark.asyncio
async def test_illegal_state_transition_rejected(db_session):
    user = User(whatsapp_user_id="wa_456", phone_number="+918888888888")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-1002",
        user_id=user.id,
        status=OrderState.CREATED.value,
    )
    db_session.add(order)
    await db_session.flush()

    # Illegal transition: CREATED -> PRINTING (unpaid/unuploaded order)
    with pytest.raises(AppException) as exc_info:
        await transition_order(db_session, order, OrderState.PRINTING, "CUSTOMER")

    assert exc_info.value.code == ErrorCode.INVALID_STATE_TRANSITION
    assert "Illegal order state transition" in exc_info.value.message


@pytest.mark.asyncio
async def test_waiting_for_print_window_and_manual_required_transitions(db_session):
    user = User(whatsapp_user_id="wa_789", phone_number="+917777777777")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-1003",
        user_id=user.id,
        status=OrderState.PAID.value,
    )
    db_session.add(order)
    await db_session.flush()

    # Closed hours transition: PAID -> WAITING_FOR_PRINT_WINDOW -> QUEUED
    await transition_order(db_session, order, OrderState.WAITING_FOR_PRINT_WINDOW, "SCHEDULER")
    assert order.status == OrderState.WAITING_FOR_PRINT_WINDOW.value

    await transition_order(db_session, order, OrderState.QUEUED, "SCHEDULER")
    assert order.status == OrderState.QUEUED.value

    # Failure flow: QUEUED -> PRINTING -> PRINT_FAILED -> MANUAL_REQUIRED -> PRINTED
    await transition_order(db_session, order, OrderState.PRINTING, "AGENT")
    await transition_order(db_session, order, OrderState.PRINT_FAILED, "AGENT")
    await transition_order(db_session, order, OrderState.MANUAL_REQUIRED, "BACKEND")
    assert order.status == OrderState.MANUAL_REQUIRED.value

    await transition_order(db_session, order, OrderState.PRINTED, "ADMIN")
    assert order.status == OrderState.PRINTED.value

