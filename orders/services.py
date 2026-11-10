from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from orders.models import Order
from products.models import Variant

MERCADO_PAGO_RESERVATION_TTL = timedelta(minutes=30)


class InsufficientStockError(Exception):
    """Raised when the requested units cannot be reserved."""

    def __init__(self, messages):
        self.messages = messages
        super().__init__("; ".join(messages))


def _lines_by_variant(order):
    """Aggregate order lines per variant.

    Two lines of the same variant must be reserved together, otherwise each
    line would pass the stock check on its own.
    """
    lines = {}
    for item in order.items.all():
        if item.variant_id is None:
            continue
        entry = lines.setdefault(
            item.variant_id, {"name": item.variant_name, "quantity": 0}
        )
        entry["quantity"] += item.quantity
    return lines


def reservation_deadline(payment_method):
    if payment_method == Order.PaymentMethod.MERCADO_PAGO:
        return timezone.now() + MERCADO_PAGO_RESERVATION_TTL
    return None


def reserve_stock(order):
    """Take the units out of stock, atomically.

    The guard lives in the UPDATE itself: a row is only decremented when the
    current stock still covers the requested quantity. Concurrent requests
    therefore serialize on the row lock and the loser updates zero rows.
    """
    lines = _lines_by_variant(order)
    errors = []

    with transaction.atomic():
        for variant_id in sorted(lines):
            entry = lines[variant_id]
            updated = Variant.objects.filter(
                id=variant_id,
                is_active=True,
                stock__gte=entry["quantity"],
            ).update(stock=F("stock") - entry["quantity"])

            if updated == 1:
                continue

            variant = Variant.objects.filter(id=variant_id).first()
            if variant is None or not variant.is_active:
                errors.append(f"{entry['name']} ya no está disponible.")
            else:
                errors.append(
                    f"{entry['name']}: solo quedan {variant.stock} unidad(es), "
                    f"solicitaste {entry['quantity']}."
                )

        if errors:
            raise InsufficientStockError(errors)

        deadline = reservation_deadline(order.payment_method)
        Order.objects.filter(pk=order.pk).update(
            stock_state=Order.StockState.RESERVED,
            expires_at=deadline,
        )

    order.stock_state = Order.StockState.RESERVED
    order.expires_at = deadline
    return order


def _reload_stock_state(order):
    """Refresh only stock_state, leaving unsaved changes on the instance intact.

    refresh_from_db(fields=[...]) reloads every field in this Django version,
    so using it here would silently discard a status change the caller already
    made on the instance.
    """
    current = (
        Order.objects.filter(pk=order.pk)
        .values_list("stock_state", flat=True)
        .first()
    )
    if current is not None:
        order.stock_state = current


def _persist_status(order, *extra_fields):
    """Write only the status-related columns.

    A full save() would also write every other column with the values this
    instance loaded, overwriting concurrent changes made by the stock
    primitives or another webhook.
    """
    order.save(update_fields=["status", "updated_at", *extra_fields])


def commit_stock(order):
    """Turn a reservation into a sale. Never touches stock.

    Returns True only for the caller that actually performed the transition,
    which is what makes repeated payment notifications harmless.
    """
    updated = Order.objects.filter(
        pk=order.pk, stock_state=Order.StockState.RESERVED
    ).update(stock_state=Order.StockState.COMMITTED, expires_at=None)

    if updated == 1:
        order.stock_state = Order.StockState.COMMITTED
        order.expires_at = None
    else:
        _reload_stock_state(order)
    return updated == 1


def release_stock(order):
    """Return the reserved units exactly once.

    Returns True only for the caller that won the transition; a second
    cancellation or a repeated webhook releases nothing.
    """
    lines = _lines_by_variant(order)

    with transaction.atomic():
        updated = Order.objects.filter(
            pk=order.pk, stock_state=Order.StockState.RESERVED
        ).update(stock_state=Order.StockState.RELEASED, expires_at=None)

        if updated != 1:
            _reload_stock_state(order)
            return False

        for variant_id in sorted(lines):
            Variant.objects.filter(id=variant_id).update(
                stock=F("stock") + lines[variant_id]["quantity"]
            )

    order.stock_state = Order.StockState.RELEASED
    order.expires_at = None
    return True


class OrderNotCancellable(Exception):
    """Raised when the order state forbids cancellation."""


def cancel_order(order: Order) -> bool:
    """Cancel an order and release its reservation exactly once.

    Returns True when the order moved to cancelled, False when it already was.
    """
    if order.status == Order.Status.CANCELLED:
        return False

    if order.status == Order.Status.DELIVERED:
        raise OrderNotCancellable("Un pedido entregado no se puede cancelar.")

    with transaction.atomic():
        # No-op unless the order still holds a reservation, so cancelling a
        # committed sale never puts phantom units back into stock.
        release_stock(order)
        order.status = Order.Status.CANCELLED
        _persist_status(order)

    return True


def expired_reservation_queryset(now=None):
    """Orders still holding stock past their payment window."""
    now = now or timezone.now()
    return Order.objects.filter(
        stock_state=Order.StockState.RESERVED,
        expires_at__isnull=False,
        expires_at__lt=now,
    )


def release_expired_reservations(now=None, dry_run=False):
    """Return the units held by reservations that outlived their window.

    Safe to run as often as you like: release_stock only wins the
    reserved -> released transition once per order.
    """
    orders = expired_reservation_queryset(now)
    if dry_run:
        return orders.count()

    released = 0
    for order in orders:
        if release_stock(order):
            released += 1
    return released


def confirm_order_payment(order: Order, payment_id: str | None = None) -> Order:
    # A late approval can arrive after the reservation expired, after an
    # earlier attempt was rejected, or while the expiry command is running.
    # Only the conditional transition tells the truth about whether the units
    # are still held, so act on its result instead of on the loaded state.
    if not commit_stock(order):
        if order.stock_state in (Order.StockState.RELEASED, Order.StockState.NONE):
            try:
                reserve_stock(order)
            except InsufficientStockError:
                # The units are gone. Keep the approved payment id as evidence
                # and leave the order for a human instead of confirming a sale
                # we cannot fulfill.
                if payment_id:
                    order.mercadopago_payment_id = payment_id
                    _persist_status(order, "mercadopago_payment_id")
                return order
            commit_stock(order)

    order.status = Order.Status.CONFIRMED
    if payment_id:
        order.mercadopago_payment_id = payment_id

    _persist_status(order, *(["mercadopago_payment_id"] if payment_id else []))
    return order


def reject_order_payment(order: Order, payment_id: str) -> Order:
    # A confirmed sale is final: a late rejection notification must not undo it.
    if order.status in (Order.Status.CONFIRMED, Order.Status.DELIVERED):
        return order

    order.status = Order.Status.PAYMENT_REJECTED
    order.mercadopago_payment_id = payment_id

    release_stock(order)
    _persist_status(order, "mercadopago_payment_id")
    return order
