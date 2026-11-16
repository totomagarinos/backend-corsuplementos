import logging
import threading

from django.conf import settings
from django.db import connection
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import mercadopago
from mercadopago.webhook import (
    InvalidWebhookSignatureError,
    WebhookSignatureValidator,
)
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Order
from orders.services import confirm_order_payment, reject_order_payment
from payments.models import PaymentEvent

logger = logging.getLogger(__name__)

PROCESSABLE_TOPICS = {"payment", "payment.created"}
SIGNATURE_TOLERANCE_SECONDS = 300


def _extract_payment_id(request):
    """Mercado Pago sends the id as ?data.id=... or inside the JSON body."""
    return (
        request.query_params.get("data.id")
        or request.query_params.get("id")
        or (request.data.get("data") or {}).get("id")
    )


def _signature_is_valid(request, payment_id):
    if not settings.MERCADOPAGO_WEBHOOK_SECRET:
        logger.error(
            "MERCADOPAGO_WEBHOOK_SECRET is not configured; rejecting webhook."
        )
        return False

    try:
        WebhookSignatureValidator.validate(
            x_signature=request.headers.get("x-signature"),
            x_request_id=request.headers.get("x-request-id"),
            data_id=payment_id,
            secret=settings.MERCADOPAGO_WEBHOOK_SECRET,
            tolerance_seconds=SIGNATURE_TOLERANCE_SECONDS,
        )
    except InvalidWebhookSignatureError as exc:
        logger.warning(
            "Rejected Mercado Pago webhook: %s (request-id=%s)",
            exc.reason.value,
            exc.request_id,
        )
        return False

    return True


def process_mp_payment(payment_id):
    """Apply one Mercado Pago payment to its order, exactly once.

    The PaymentEvent row is the idempotency gate: only the caller that creates
    it processes the payment. Any failure deletes the row so Mercado Pago's
    retry can try again.
    """
    event, created = PaymentEvent.objects.get_or_create(payment_id=str(payment_id))
    if not created:
        return

    try:
        sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
        payment_info = sdk.payment().get(payment_id)

        if payment_info["status"] != 200:
            event.delete()
            return

        payment = payment_info["response"]
        event.status = payment.get("status", "")
        event.save(update_fields=["status"])

        try:
            order = Order.objects.get(id=payment["external_reference"])
        except (Order.DoesNotExist, KeyError, TypeError):
            return

        event.order = order
        event.save(update_fields=["order"])

        if payment["status"] == "approved":
            confirm_order_payment(order, payment_id=str(payment_id))
        elif payment["status"] in ("rejected", "cancelled"):
            reject_order_payment(order, payment_id=str(payment_id))
    except Exception:
        event.delete()
        raise


def _run_payment_processor(payment_id):
    """Thread entry point: own the connection and never lose the error."""
    try:
        process_mp_payment(payment_id)
    except Exception:
        logger.exception("Failed to process Mercado Pago payment %s", payment_id)
    finally:
        # This runs in a worker thread: Django leaves the connection open.
        connection.close()


@method_decorator(csrf_exempt, name="dispatch")
class MercadoPagoWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        topic = (
            request.query_params.get("type")
            or request.query_params.get("topic")
            or request.data.get("type")
        )
        payment_id = _extract_payment_id(request)

        # Other Mercado Pago topics are acknowledged and ignored.
        if topic not in PROCESSABLE_TOPICS:
            return Response(status=status.HTTP_200_OK)

        if not payment_id:
            return Response(
                {"detail": "Falta el id del pago."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if settings.MERCADOPAGO_WEBHOOK_VERIFY_SIGNATURE and not _signature_is_valid(
            request, payment_id
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        thread = threading.Thread(
            target=_run_payment_processor,
            args=(payment_id,),
            daemon=True,
        )
        thread.start()

        # Respond fast: Mercado Pago retries when it does not get a 2xx.
        return Response(status=status.HTTP_200_OK)
