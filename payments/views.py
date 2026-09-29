from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import mercadopago
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from orders.models import Order
from orders.services import confirm_order_payment, reject_order_payment
import threading


def process_mp_payment(payment_id):
    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
    payment_info = sdk.payment().get(payment_id)

    # Validamos que la respuesta sea correcta
    if payment_info["status"] != 200:
        return

    payment = payment_info["response"]

    # Procesamos la orden
    try:
        order = Order.objects.get(id=payment["external_reference"])
    except Order.DoesNotExist:
        return

    if str(order.mercadopago_payment_id) == str(payment_id):
        return

    # Actualizamos el estado
    if payment["status"] == "approved":
        confirm_order_payment(order, payment_id=str(payment_id))
    elif payment["status"] in ["rejected", "cancelled"]:
        reject_order_payment(order, payment_id=str(payment_id))


@method_decorator(csrf_exempt, name="dispatch")
class MercadoPagoWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # 1. Buscamos el ID del pago
        topic = (
            request.query_params.get("topic")
            or request.query_params.get("type")
            or request.data.get("type")
        )
        payment_id = request.query_params.get("id")

        if not payment_id and "data" in request.data:
            payment_id = request.data["data"].get("id")

        if topic not in ["payment", "payment.created"] or not payment_id:
            return Response(status=200)

        # 2. Delegamos la lógica pesada a un hilo secundario
        thread = threading.Thread(target=process_mp_payment, args=(payment_id,))
        thread.start()

        # SIEMPRE retornar 200 OK rápido para que MP no reintente
        return Response(status=200)
