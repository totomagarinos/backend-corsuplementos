import hashlib
import hmac
import time
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from orders.models import Order
from payments.models import PaymentEvent
from payments.views import process_mp_payment
from products.models import Product, Variant
from shipping.models import ShippingOption
from users.models import CustomUser

WEBHOOK_SECRET = "test-webhook-secret"
WEBHOOK_URL = "/api/payments/webhook/"


def build_signature(payment_id, request_id="request-1", ts=None, secret=WEBHOOK_SECRET):
    ts = str(int(time.time())) if ts is None else str(ts)
    manifest = f"id:{payment_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={digest}"


@override_settings(
    MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET,
    MERCADOPAGO_WEBHOOK_VERIFY_SIGNATURE=True,
)
class MercadoPagoWebhookSignatureTest(TestCase):
    """R7: only notifications signed by Mercado Pago may be processed."""

    def setUp(self):
        self.client = APIClient()

    def _post(self, query, signature=None, request_id="request-1"):
        headers = {}
        if signature is not None:
            headers["HTTP_X_SIGNATURE"] = signature
        if request_id is not None:
            headers["HTTP_X_REQUEST_ID"] = request_id
        return self.client.post(f"{WEBHOOK_URL}{query}", {}, format="json", **headers)

    @patch("payments.views.threading.Thread")
    def test_valid_signature_is_dispatched(self, mock_thread):
        response = self._post(
            "?data.id=payment-123&type=payment",
            signature=build_signature("payment-123"),
        )

        self.assertEqual(response.status_code, 200)
        mock_thread.assert_called_once()
        _, kwargs = mock_thread.call_args
        self.assertEqual(kwargs["args"], ("payment-123",))
        mock_thread.return_value.start.assert_called_once()

    @patch("payments.views.threading.Thread")
    def test_wrong_signature_is_rejected(self, mock_thread):
        response = self._post(
            "?data.id=payment-123&type=payment",
            signature="ts=1,v1=deadbeef",
        )

        self.assertEqual(response.status_code, 403)
        mock_thread.assert_not_called()

    @patch("payments.views.threading.Thread")
    def test_missing_signature_header_is_rejected(self, mock_thread):
        response = self._post("?data.id=payment-123&type=payment")

        self.assertEqual(response.status_code, 403)
        mock_thread.assert_not_called()

    @patch("payments.views.threading.Thread")
    def test_stale_timestamp_is_rejected(self, mock_thread):
        stale = int(time.time()) - 3600
        response = self._post(
            "?data.id=payment-123&type=payment",
            signature=build_signature("payment-123", ts=stale),
        )

        self.assertEqual(response.status_code, 403)
        mock_thread.assert_not_called()

    @patch("payments.views.threading.Thread")
    def test_signature_for_another_payment_is_rejected(self, mock_thread):
        response = self._post(
            "?data.id=payment-123&type=payment",
            signature=build_signature("payment-999"),
        )

        self.assertEqual(response.status_code, 403)
        mock_thread.assert_not_called()

    @patch("payments.views.threading.Thread")
    def test_other_topics_are_acknowledged_and_ignored(self, mock_thread):
        response = self._post("?data.id=whatever&type=merchant_order")

        self.assertEqual(response.status_code, 200)
        mock_thread.assert_not_called()

    @patch("payments.views.threading.Thread")
    def test_payment_without_id_is_a_bad_request(self, mock_thread):
        response = self._post("?type=payment")

        self.assertEqual(response.status_code, 400)
        mock_thread.assert_not_called()

    @override_settings(MERCADOPAGO_WEBHOOK_SECRET="")
    @patch("payments.views.threading.Thread")
    def test_missing_secret_fails_closed(self, mock_thread):
        response = self._post(
            "?data.id=payment-123&type=payment",
            signature=build_signature("payment-123", secret=""),
        )

        self.assertEqual(response.status_code, 403)
        mock_thread.assert_not_called()

    @override_settings(MERCADOPAGO_WEBHOOK_VERIFY_SIGNATURE=False)
    @patch("payments.views.threading.Thread")
    def test_verification_can_be_disabled_for_local_development(self, mock_thread):
        response = self._post("?data.id=payment-123&type=payment")

        self.assertEqual(response.status_code, 200)
        mock_thread.return_value.start.assert_called_once()


class MercadoPagoIdempotencyTest(TestCase):
    """R7: Mercado Pago retries must not duplicate any operation."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="webhook-buyer@example.com",
            email="webhook-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Zinc",
            slug="zinc",
            description="Test product",
            price=Decimal("2000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="60 caps",
            flavor="Unflavored",
            sku="WH-001",
            stock=10,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_mp_order(self):
        with (
            patch("orders.serializers.create_payment_preference") as mock_preference,
            patch("orders.serializers.send_new_order_email_to_admin"),
        ):
            mock_preference.return_value = {
                "id": "pref-webhook",
                "init_point": "https://mp/pref-webhook",
            }
            response = self.client.post(
                "/api/orders/",
                {
                    "customer_name": "Test Buyer",
                    "customer_email": self.user.email,
                    "customer_phone": "2640000000",
                    "shipping_option": self.shipping_option.id,
                    "payment_method": Order.PaymentMethod.MERCADO_PAGO,
                    "items": [{"variant": self.variant.id, "quantity": 1}],
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(id=response.data["id"])

    @staticmethod
    def _payment(order, payment_status):
        return {
            "status": 200,
            "response": {
                "external_reference": str(order.id),
                "status": payment_status,
            },
        }

    @patch("payments.views.mercadopago.SDK")
    def test_duplicate_notification_is_processed_once(self, mock_sdk_class):
        order = self._create_mp_order()
        mock_sdk_class.return_value.payment.return_value.get.return_value = (
            self._payment(order, "approved")
        )

        process_mp_payment("payment-1")
        process_mp_payment("payment-1")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("payments.views.mercadopago.SDK")
    def test_rejection_then_approval_records_both_attempts(self, mock_sdk_class):
        order = self._create_mp_order()
        mock_sdk_class.return_value.payment.return_value.get.side_effect = [
            self._payment(order, "rejected"),
            self._payment(order, "approved"),
        ]

        process_mp_payment("payment-1")
        process_mp_payment("payment-2")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("payments.views.mercadopago.SDK")
    def test_failed_lookup_does_not_block_a_retry(self, mock_sdk_class):
        order = self._create_mp_order()
        mock_sdk_class.return_value.payment.return_value.get.side_effect = [
            {"status": 500, "response": {}},
            self._payment(order, "approved"),
        ]

        process_mp_payment("payment-1")
        self.assertEqual(PaymentEvent.objects.count(), 0)

        process_mp_payment("payment-1")

        order.refresh_from_db()
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(order.status, Order.Status.CONFIRMED)

    @patch("payments.views.mercadopago.SDK")
    def test_exception_releases_the_event_for_a_retry(self, mock_sdk_class):
        order = self._create_mp_order()
        mock_sdk_class.return_value.payment.return_value.get.side_effect = [
            RuntimeError("mercado pago exploded"),
            self._payment(order, "approved"),
        ]

        with self.assertRaises(RuntimeError):
            process_mp_payment("payment-1")

        self.assertEqual(PaymentEvent.objects.count(), 0)

        process_mp_payment("payment-1")

        order.refresh_from_db()
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(order.status, Order.Status.CONFIRMED)

    @patch("payments.views.mercadopago.SDK")
    def test_payment_without_order_is_recorded_without_side_effects(
        self, mock_sdk_class
    ):
        mock_sdk_class.return_value.payment.return_value.get.return_value = {
            "status": 200,
            "response": {"external_reference": "999999", "status": "approved"},
        }

        process_mp_payment("payment-orphan")

        event = PaymentEvent.objects.get(payment_id="payment-orphan")
        self.assertIsNone(event.order)
        self.assertEqual(event.status, "approved")
