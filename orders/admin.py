from django.contrib import admin

from orders.models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "customer_name", "total", "status", "created_at"]
    list_editable = ["status"]
    list_filter = ["status"]
    inlines = [OrderItemInline]
    readonly_fields = [
        "user",
        "subtotal",
        "total",
        "shipping_cost",
        "created_at",
    ]


admin.site.register(Order, OrderAdmin)
