from django.contrib import admin

from orders.models import Order, OrderItem
from .services import confirm_order_payment


@admin.action(description="Marcar pedido seleccionados como PAGADOS (Efectivo)")
def mark_as_confirmed(modeladmin, request, queryset):
    for order in queryset:
        if order.status != Order.Status.CONFIRMED:
            confirm_order_payment(order)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "customer_name", "total", "status", "created_at"]
    list_editable = ["status"]
    list_filter = ["status"]
    actions = [mark_as_confirmed]
    inlines = [OrderItemInline]
    readonly_fields = [
        "user",
        "subtotal",
        "total",
        "shipping_cost",
        "created_at",
    ]


admin.site.register(Order, OrderAdmin)
