import io
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from products.management.commands.seed_product_images import DEFAULT_FOLDER
from products.management.commands.seed_products import CATALOG
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


class SeedProductImagesTest(TestCase):
    """The image seeder has to survive a catalogue created without images."""

    def setUp(self):
        item = CATALOG[0]
        self.product = Product.objects.create(
            brand=item["brand"],
            name=item["name"],
            slug=item["slug"],
            description="demo",
            category=item["category"],
            price=item["price"],
        )
        Variant.objects.create(
            product=self.product,
            size=item["sizes"][0],
            flavor=item["flavors"][0],
            sku="SEED-001",
            stock=5,
        )

    def test_uploads_an_image_for_a_product_that_has_none(self):
        # `seed_products` creates every product with an empty image field.
        # Reading it with str() used to raise TypeError before the upload, so
        # the whole command died on the first product.
        self.assertFalse(self.product.image)

        with (
            patch(
                "products.management.commands.seed_product_images.render_mockup",
                return_value=io.BytesIO(b"fake-image"),
            ),
            patch("cloudinary.uploader.upload") as upload,
        ):
            upload.return_value = {
                "public_id": f"{DEFAULT_FOLDER}/demo-{self.product.slug}"
            }
            call_command("seed_product_images")

        self.product.refresh_from_db()
        self.assertEqual(
            str(self.product.image), f"{DEFAULT_FOLDER}/demo-{self.product.slug}"
        )
        upload.assert_called_once()

