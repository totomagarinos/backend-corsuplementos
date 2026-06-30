from rest_framework import serializers

from shipping.models import ShippingOption


class ShippingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingOption
        fields = ["id", "name", "description", "price", "estimated_days", "type"]
