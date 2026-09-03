from rest_framework import serializers

from orders.models import Order, OrderItem
from payments.services import create_payment_preference


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["variant", "quantity", "variant_name", "price", "subtotal"]
        extra_kwargs = {
            "variant": {"write_only": True},
            "variant_name": {"read_only": True},
            "price": {"read_only": True},
            "subtotal": {"read_only": True},
        }


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    payment_url = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "user",
            "customer_name",
            "customer_email",
            "customer_phone",
            "shipping_option",
            "shipping_address",
            "shipping_cost",
            "subtotal",
            "total",
            "notes",
            "status",
            "payment_method",
            "payment_url",
            "created_at",
            "items",
        ]
        read_only_fields = ["subtotal", "total", "status"]

    def get_payment_url(self, obj):
        return getattr(obj, "_payment_url", None)

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        validated_data["user"] = self.context["request"].user

        order = Order.objects.create(**validated_data)

        order_subtotal = 0

        for item_data in items_data:
            variant = item_data["variant"]
            quantity = item_data["quantity"]
            price = variant.price
            item_subtotal = price * quantity
            order_subtotal += item_subtotal

            OrderItem.objects.create(
                order=order,
                variant=variant,
                variant_name=str(variant),
                quantity=quantity,
                price=price,
                subtotal=item_subtotal,
            )

        order.subtotal = order_subtotal
        order.total = order_subtotal + order.shipping_cost
        order.save(update_fields=["subtotal", "total"])

        if order.payment_method == Order.PaymentMethod.MERCADO_PAGO:
            preference = create_payment_preference(order)
            order.mercadopago_preference_id = preference["id"]
            order.save(update_fields=["mercadopago_preference_id"])
            order._payment_url = preference["init_point"]

        return order
