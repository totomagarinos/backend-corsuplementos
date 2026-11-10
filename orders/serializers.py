from decimal import Decimal

from rest_framework import serializers
from django.db import transaction

from orders.models import Order, OrderItem
from orders.services import InsufficientStockError, reserve_stock
from payments.services import create_payment_preference
from shipping.models import ShippingOption
import threading
from .notifications import send_new_order_email_to_admin


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
        read_only_fields = ["subtotal", "total", "shipping_cost", "status"]

    def get_payment_url(self, obj):
        return getattr(obj, "_payment_url", None)

    def validate_items(self, value):
        # Aggregate first: two lines of the same variant must be checked
        # together, otherwise each line passes on its own.
        requested_by_variant = {}
        for item in value:
            variant = item["variant"]
            requested_by_variant[variant] = (
                requested_by_variant.get(variant, 0) + item["quantity"]
            )

        errors = []
        for variant, requested in requested_by_variant.items():
            if not variant.is_active or not variant.product.is_active:
                errors.append(f"{variant} ya no está disponible.")
            elif variant.stock < requested:
                errors.append(
                    f"{variant}: solo quedan {variant.stock} unidad(es), "
                    f"solicitaste {requested}."
                )

        if errors:
            raise serializers.ValidationError(errors)

        return value

    def validate(self, attrs):
        shipping_option = attrs.get("shipping_option")
        payment_method = attrs.get("payment_method", Order.PaymentMethod.MERCADO_PAGO)

        if shipping_option is not None and not shipping_option.is_active:
            raise serializers.ValidationError(
                {"shipping_option": "La opción de envío elegida no está disponible."}
            )

        # Cash is collected in person, so the order must be picked up.
        if (
            payment_method == Order.PaymentMethod.CASH
            and shipping_option is not None
            and shipping_option.type != ShippingOption.Type.LOCAL_PICKUP
        ):
            raise serializers.ValidationError(
                {
                    "payment_method": (
                        "El pago en efectivo solo está disponible para retiro "
                        "en el local."
                    )
                }
            )

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        user = self.context["request"].user
        validated_data["user"] = user
        # Shipping is never charged online: Uber delivery is paid to the driver.
        validated_data["shipping_cost"] = Decimal("0")

        with transaction.atomic():
            order = Order.objects.create(**validated_data)

            order_subtotal = 0

            for item_data in items_data:
                variant = item_data["variant"]
                quantity = item_data["quantity"]

                if getattr(user, "is_vip", False) and variant.product.vip_price:
                    price = variant.product.vip_price
                else:
                    price = variant.product.price

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
            order.total = order_subtotal
            order.save(update_fields=["subtotal", "total"])

            # Reserve before creating the payment preference: never offer a
            # checkout link for units we do not hold.
            try:
                reserve_stock(order)
            except InsufficientStockError as exc:
                raise serializers.ValidationError({"items": exc.messages})

            if order.payment_method == Order.PaymentMethod.MERCADO_PAGO:
                preference = create_payment_preference(order)
                order.mercadopago_preference_id = preference["id"]
                order.save(update_fields=["mercadopago_preference_id"])
                order._payment_url = preference["init_point"]

            email_thread = threading.Thread(
                target=send_new_order_email_to_admin, args=(order,)
            )
            email_thread.start()

            return order
