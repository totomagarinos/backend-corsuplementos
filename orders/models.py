from users.models import CustomUser
from django.db import models, transaction

from products.models import Variant
from shipping.models import ShippingOption


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Confirmado"
        CANCELLED = "cancelled", "Cancelado"
        DELIVERED = "delivered", "Entregado"
        PAYMENT_REJECTED = "payment_rejected", "Pago rechazado"

    class PaymentMethod(models.TextChoices):
        MERCADO_PAGO = "mercado_pago", " Mercado Pago"
        CASH = "efectivo", "Efectivo"

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="orders",
    )
    customer_name = models.CharField(max_length=100)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=50)

    shipping_option = models.ForeignKey(
        ShippingOption, on_delete=models.SET_NULL, null=True
    )
    shipping_address = models.TextField(blank=True)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.MERCADO_PAGO
    )
    mercadopago_preference_id = models.CharField(max_length=100, blank=True, default="")
    mercadopago_payment_id = models.CharField(max_length=100, blank=True, default="")
    stock_deducted = models.BooleanField(default=False)

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._original_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._original_status = self.status
            return super().save(*args, **kwargs)

        is_transitioning_to_confirmed = (
            self.status == self.Status.CONFIRMED
            and self._original_status != self.Status.CONFIRMED
        )
        should_deduct = is_transitioning_to_confirmed and not self.stock_deducted

        with transaction.atomic():
            if should_deduct:
                for item in self.items.all():
                    if item.variant:
                        Variant.objects.filter(id=item.variant.id).update(
                            stock=models.F("stock") - item.quantity
                        )
                self.stock_deducted = True

            if is_transitioning_to_confirmed:
                if self.user and self.total >= 10000 and not self.user.is_vip:
                    self.user.is_vip = True
                    self.user.save(update_fields=["is_vip"])

            super().save(*args, **kwargs)
            self._original_status = self.status

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.id} - {self.customer_name} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.SET_NULL, null=True)
    variant_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.variant_name} x{self.quantity}"
