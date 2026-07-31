from django.contrib.auth.models import User
from django.db import models, transaction

from products.models import Variant
from shipping.models import ShippingOption


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Confirmado"
        CANCELLED = "cancelled", "Cancelado"
        DELIVERED = "entregado", "Entregado"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="orders"
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

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._original_status = self.status
            return super().save(*args, **kwargs)

        with transaction.atomic():
            if (
                self._original_status == self.Status.PENDING
                and self.status == self.Status.CONFIRMED
            ):
                for item in self.items.all():
                    if item.variant:
                        Variant.objects.filter(id=item.variant.id).update(
                            stock=models.F("stock") - item.quantity
                        )
            super().save(*args, **kwargs)

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
