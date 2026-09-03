from orders.models import Order


def confirm_order_payment(order: Order, payment_id: str | None = None) -> Order:
    order.status = Order.Status.CONFIRMED
    if payment_id:
        order.mercadopago_payment_id = payment_id
    order.save()
    return order


def reject_order_payment(order: Order, payment_id: str) -> Order:
    order.status = Order.Status.PAYMENT_REJECTED
    order.mercadopago_payment_id = payment_id
    order.save()
    return order
