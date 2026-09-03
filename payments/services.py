import mercadopago
from django.conf import settings


def create_payment_preference(order):
    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)

    items = [
        {
            "title": item.variant_name,
            "quantity": item.quantity,
            "unit_price": float(item.price),
        }
        for item in order.items.all()
    ]

    preference_data = {
        "items": items,
        "external_reference": str(order.id),
        "payment_methods": {
            "excluded_payment_types": [{"id": "ticket"}, {"id": "atm"}]
        },
        "back_urls": {
            "success": f"{settings.FRONTEND_URL}/order/{order.id}/confirmation",
            "failure": f"{settings.FRONTEND_URL}/order/{order.id}/confirmation",
            "pending": f"{settings.FRONTEND_URL}/order/{order.id}/confirmation",
        },
        "notification_url": f"{settings.BACKEND_URL}/api/payments/webhook/",
    }

    result = sdk.preference().create(preference_data)
    return result["response"]
