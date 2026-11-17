from django.contrib import admin
from django.utils import timezone

from orders.models import Order, OrderItem
from .services import (
    OrderNotCancellable,
    cancel_order,
    confirm_order_payment,
    release_stock,
)

CONFIRMABLE_STATUSES = (Order.Status.PENDING, Order.Status.PAYMENT_REJECTED)


@admin.action(description="Marcar pedidos seleccionados como PAGADOS")
def mark_as_confirmed(modeladmin, request, queryset):
    confirmed = 0
    skipped = 0
    unconfirmed = []

    for order in queryset:
        if order.status not in CONFIRMABLE_STATUSES:
            skipped += 1
            continue

        confirm_order_payment(order)

        # The units can be gone by the time the payment is approved: the
        # reservation expired, or the customer cancelled while the payment was
        # in flight, and somebody else bought them. In that case the order
        # stays pending, so counting it as confirmed would report a sale that
        # never happened.
        if order.status == Order.Status.CONFIRMED:
            confirmed += 1
        else:
            unconfirmed.append(order.pk)

    message = f"{confirmed} pedido(s) confirmado(s); {skipped} sin cambios."
    if unconfirmed:
        ids = ", ".join(f"#{pk}" for pk in unconfirmed)
        message += f" {len(unconfirmed)} sin stock o con variante inactiva: {ids}."

    modeladmin.message_user(request, message)


@admin.action(description="Marcar pedidos seleccionados como ENTREGADOS")
def mark_as_delivered(modeladmin, request, queryset):
    delivered = queryset.filter(status=Order.Status.CONFIRMED).update(
        status=Order.Status.DELIVERED, updated_at=timezone.now()
    )
    skipped = queryset.count() - delivered

    modeladmin.message_user(
        request, f"{delivered} pedido(s) entregado(s); {skipped} sin cambios."
    )


@admin.action(description="Cancelar pedidos seleccionados y liberar stock")
def mark_as_cancelled(modeladmin, request, queryset):
    cancelled = 0
    skipped = 0

    for order in queryset:
        try:
            if cancel_order(order):
                cancelled += 1
            else:
                skipped += 1
        except OrderNotCancellable:
            skipped += 1

    modeladmin.message_user(
        request, f"{cancelled} pedido(s) cancelado(s); {skipped} sin cambios."
    )


@admin.action(description="Liberar reservas vencidas de los seleccionados")
def mark_as_expired(modeladmin, request, queryset):
    now = timezone.now()
    released = 0
    skipped = 0

    for order in queryset:
        if (
            order.stock_state == Order.StockState.RESERVED
            and order.expires_at is not None
            and order.expires_at < now
        ):
            if release_stock(order):
                released += 1
                continue
        skipped += 1

    modeladmin.message_user(
        request,
        f"{released} reserva(s) vencida(s) liberada(s); {skipped} sin cambios.",
    )


class ExpiredReservationFilter(admin.SimpleListFilter):
    title = "reserva vencida"
    parameter_name = "expired"

    def lookups(self, request, model_admin):
        return (("yes", "Sí"), ("no", "No"))

    def queryset(self, request, queryset):
        expired = {
            "stock_state": Order.StockState.RESERVED,
            "expires_at__lt": timezone.now(),
        }
        if self.value() == "yes":
            return queryset.filter(**expired)
        if self.value() == "no":
            return queryset.exclude(**expired)
        return queryset


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "customer_name",
        "total",
        "status",
        "stock_state",
        "created_at",
    ]
    list_filter = ["status", "stock_state", ExpiredReservationFilter]
    actions = [
        mark_as_confirmed,
        mark_as_delivered,
        mark_as_cancelled,
        mark_as_expired,
    ]
    inlines = [OrderItemInline]
    readonly_fields = [
        "user",
        "status",
        "subtotal",
        "total",
        "shipping_cost",
        "stock_state",
        "expires_at",
        "mercadopago_preference_id",
        "mercadopago_payment_id",
        "created_at",
    ]


admin.site.register(Order, OrderAdmin)
