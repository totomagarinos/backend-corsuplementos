from django.db import models
from cloudinary.models import CloudinaryField


class Product(models.Model):
    class Category(models.TextChoices):
        WHEY = "whey", "Whey Protein"
        PLANT_PROTEIN = "plant_protein", "Plant-Based Protein"
        CREATINE = "creatine", "Creatine"
        ISOTONICS = "isotonics", "Hydromax Isotonics"
        RECOVERY = "recovery", "Recovery Drinks"
        GELS = "gels", "Energy Gels"
        CAFFEINE = "caffeine", "Caffeine Capsules"
        MAGNESIUM = "magnesium", "Magnesium Citrate"
        OMEGA3 = "omega3", "Omega-3"
        MULTIVITAMINS = "multivitamins", "Multivitamins"
        OTHER = "other", "Otros"

    brand = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    image = CloudinaryField("image")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.brand} - {self.name}"


class Variant(models.Model):
    product = models.ForeignKey(
        Product, related_name="variants", on_delete=models.CASCADE
    )
    size = models.CharField(max_length=50)
    flavor = models.CharField(max_length=100)
    sku = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ["product", "size", "flavor"]
        ordering = ["size"]

    def __str__(self):
        return f"{self.product.name} - {self.size} / {self.flavor}"
