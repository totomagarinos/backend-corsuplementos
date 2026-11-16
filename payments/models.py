from django.db import models


class PaymentEvent(models.Model):
    """One Mercado Pago notification that was already processed.

    payment_id is unique on purpose: when Mercado Pago retries or duplicates a
    notification, only the request that creates the row processes the payment.
    The database decides the winner, not a Python comparison.
    """

    payment_id = models.CharField(max_length=100, unique=True)
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_events",
    )
    status = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.payment_id} ({self.status or 'unknown'})"
