from rest_framework import serializers

from .models import Product, Variant


class VariantSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(
        source="product.price", max_digits=10, decimal_places=2, read_only=True
    )
    vip_price = serializers.DecimalField(
        source="product.vip_price", max_digits=10, decimal_places=2, read_only=True
    )
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = Variant
        fields = [
            "id",
            "sku",
            "size",
            "flavor",
            "product_name",
            "price",
            "vip_price",
            "stock",
        ]


class ProductSerializer(serializers.ModelSerializer):
    variants = VariantSerializer(many=True, read_only=True)

    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )

    image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "brand",
            "name",
            "slug",
            "description",
            "category",
            "category_display",
            "price",
            "vip_price",
            "image",
            "variants",
        ]

    def get_image(self, obj):
        if obj.image:
            return obj.image.build_url()
        return None
