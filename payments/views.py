from django.conf import settings
import mercadopago
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from orders.models import Order
from orders.services import confirm_order_payment, reject_order_payment


class MercadoPagoWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # 1. Buscamos el ID del pago en Query Params (IPN) o en el Body (Webhooks V2)
        topic = (
            request.query_params.get("topic")
            or request.query_params.get("type")
            or request.data.get("type")
        )
        payment_id = request.query_params.get("id")

        # Si viene en el body bajo 'data' (Webhook v2)
        if not payment_id and "data" in request.data:
            payment_id = request.data["data"].get("id")

        # MP a veces envía eventos de prueba (test.created). Solo nos interesa 'payment'
        if topic not in ["payment", "payment.created"]:
            return Response(status=200)

        if not payment_id:
            return Response(status=200)

        # 2. Consultamos a Mercado Pago por el estado real de este pago (Seguridad)
        sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
        payment_info = sdk.payment().get(payment_id)

        # Validamos que la respuesta sea correcta
        if payment_info["status"] != 200:
            return Response(status=200)

        payment = payment_info["response"]

        # 3. Procesamos la orden
        try:
            order = Order.objects.get(id=payment["external_reference"])
        except Order.DoesNotExist:
            return Response(status=200)

        if str(order.mercadopago_payment_id) == str(payment_id):
            return Response(status=200)

        # 4. Actualizamos el estado
        if payment["status"] == "approved":
            confirm_order_payment(order, payment_id=str(payment_id))
        elif payment["status"] in ["rejected", "cancelled"]:
            reject_order_payment(order, payment_id=str(payment_id))

        # SIEMPRE retornar 200 OK rápido para que MP no reintente
        return Response(status=200)
