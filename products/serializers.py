from rest_framework import serializers

from .models import Product, Variant


class VariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variant
        fields = ["id", "sku", "size", "flavor", "price", "stock"]


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
            "base_price",
            "image",
            "variants",
        ]

    def get_image(self, obj):
        if obj.image:
            return obj.image.build_url()
        return None
