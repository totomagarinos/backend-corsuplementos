from django.contrib import admin

from payments.models import PaymentEvent


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    """Read-only audit trail of processed Mercado Pago notifications."""

    list_display = ["payment_id", "order", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["payment_id"]
    readonly_fields = ["payment_id", "order", "status", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
