from django.test import TestCase

from products.models import Product, Variant


class ProductVariantModelTest(TestCase):
    def test_variant_contains_product_name(self):
        product = Product.objects.create(
            brand="brand1",
            name="name1",
            slug="slug1",
            description="description1",
            price="10.00",
        )

        variant = Variant.objects.create(
            product=product,
            size="size1",
            flavor="flavor1",
            sku="TEST-001",
        )

        self.assertIn(product.name, str(variant))
