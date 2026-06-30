from django.db import models


class ShippingOption(models.Model):
    class Type(models.TextChoices):
        LOCAL_PICKUP = "pickup", "Retiro en Local"
        LOCAL_DELIVERY = "delivery", "Envío Local (Uber)"
        NATIONAL = "national", "Envío Nacional"

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_days = models.CharField(max_length=50, blank=True)
    type = models.CharField(max_length=20, choices=Type.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["type", "price"]

    def __str__(self):
        if self.price is None:
            return f"{self.name} - Precio a confirmar"
        return f"{self.name} - ${self.price}"
