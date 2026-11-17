import threading
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from orders.models import Order
from orders.serializers import OrderSerializer
from orders.services import (
    confirm_order_payment,
    release_expired_reservations,
    release_stock,
    reject_order_payment,
)
from payments.views import process_mp_payment
from products.models import Product, Variant
from shipping.models import ShippingOption
from users.models import CustomUser


class OrderAmountIntegrityTest(TestCase):
    """R8: the client must never be able to influence order amounts."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="buyer@example.com",
            email="buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Whey Protein",
            slug="whey-protein",
            description="Test product",
            price=Decimal("10000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="1kg",
            flavor="Chocolate",
            sku="R8-001",
            stock=10,
        )
        # Local Uber delivery: price is unknown at order time (paid to the driver).
        self.shipping_option = ShippingOption.objects.create(
            name="Envio local (Uber)",
            type=ShippingOption.Type.LOCAL_DELIVERY,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _payload(self, **overrides):
        payload = {
            "customer_name": "Test Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "2640000000",
            "shipping_option": self.shipping_option.id,
            "shipping_address": "Calle Falsa 123, San Juan",
            "payment_method": Order.PaymentMethod.MERCADO_PAGO,
            "items": [{"variant": self.variant.id, "quantity": 1}],
        }
        payload.update(overrides)
        return payload

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_client_supplied_shipping_cost_is_ignored(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-123",
            "init_point": "https://www.mercadopago.com/checkout/pref-123",
        }

        response = self.client.post(
            "/api/orders/",
            self._payload(shipping_cost="999999.00"),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)

        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.subtotal, Decimal("10000.00"))
        self.assertEqual(order.shipping_cost, Decimal("0"))
        self.assertEqual(order.total, order.subtotal)
        self.assertEqual(response.data["shipping_cost"], "0.00")
        self.assertEqual(response.data["total"], "10000.00")

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("payments.services.mercadopago.SDK")
    def test_mercadopago_preference_charges_products_only(
        self, mock_sdk_class, mock_send_email
    ):
        mock_sdk = mock_sdk_class.return_value
        mock_sdk.preference.return_value.create.return_value = {
            "status": 201,
            "response": {"id": "pref-456", "init_point": "https://mp/pref-456"},
        }

        response = self.client.post(
            "/api/orders/",
            self._payload(shipping_cost="999999.00"),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

        preference_data = mock_sdk.preference.return_value.create.call_args[0][0]

        # Only product lines are sent to Mercado Pago: no shipping line exists.
        self.assertEqual(len(preference_data["items"]), 1)
        item = preference_data["items"][0]
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(Decimal(str(item["unit_price"])), Decimal("10000.00"))

        charged_by_mercadopago = sum(
            Decimal(str(line["unit_price"])) * line["quantity"]
            for line in preference_data["items"]
        )
        self.assertEqual(charged_by_mercadopago, Decimal("10000.00"))

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_total_ignores_shipping_cost_set_internally(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-789",
            "init_point": "https://mp/pref-789",
        }

        # Simulates an internal writer (admin action, data import, future code)
        # that bypasses HTTP validation and sets a non-zero shipping cost.
        request = APIRequestFactory().post("/api/orders/")
        request.user = self.user

        order = OrderSerializer(context={"request": request}).create(
            {
                "customer_name": "Test Buyer",
                "customer_email": "buyer@example.com",
                "customer_phone": "2640000000",
                "shipping_option": self.shipping_option,
                "shipping_address": "Calle Falsa 123, San Juan",
                "shipping_cost": Decimal("5000.00"),
                "payment_method": Order.PaymentMethod.MERCADO_PAGO,
                "items": [{"variant": self.variant, "quantity": 1}],
            }
        )
        order.refresh_from_db()

        self.assertEqual(order.shipping_cost, Decimal("0"))
        self.assertEqual(order.total, order.subtotal)
        self.assertEqual(order.total, Decimal("10000.00"))


class OrderStockValidationTest(TestCase):
    """R1: no order may be created without an active variant and enough stock."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="stock-buyer@example.com",
            email="stock-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Creatine",
            slug="creatine",
            description="Test product",
            price=Decimal("5000.00"),
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _variant(self, **overrides):
        data = {
            "product": self.product,
            "size": "300g",
            "flavor": "Unflavored",
            "sku": "R1-001",
            "stock": 1,
        }
        data.update(overrides)
        return Variant.objects.create(**data)

    def _payload(self, variant, quantity):
        return {
            "customer_name": "Test Buyer",
            "customer_email": "stock-buyer@example.com",
            "customer_phone": "2640000000",
            "shipping_option": self.shipping_option.id,
            "payment_method": Order.PaymentMethod.MERCADO_PAGO,
            "items": [{"variant": variant.id, "quantity": quantity}],
        }

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_order_with_quantity_above_stock_is_rejected(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-1",
            "init_point": "https://mp/pref-1",
        }
        variant = self._variant(stock=1)

        response = self.client.post(
            "/api/orders/",
            self._payload(variant, quantity=3),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Order.objects.count(), 0)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 1)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_order_with_inactive_variant_is_rejected(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-2",
            "init_point": "https://mp/pref-2",
        }
        variant = self._variant(sku="R1-002", is_active=False, stock=10)

        response = self.client.post(
            "/api/orders/",
            self._payload(variant, quantity=1),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Order.objects.count(), 0)


class OrderStockConcurrencyTest(TransactionTestCase):
    """R2: two simultaneous buyers must never take the same last unit.

    Uses TransactionTestCase because threads open their own connections and
    cannot see data wrapped in a TestCase transaction.
    """

    def setUp(self):
        self.user_one = CustomUser.objects.create_user(
            username="racer-one@example.com",
            email="racer-one@example.com",
            password="test-password-123",
        )
        self.user_two = CustomUser.objects.create_user(
            username="racer-two@example.com",
            email="racer-two@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Isotonic",
            slug="isotonic",
            description="Test product",
            price=Decimal("3000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="500ml",
            flavor="Lemon",
            sku="R2-001",
            stock=1,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )

    def _buy_last_unit(self, user, barrier, results):
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            barrier.wait(timeout=5)
            response = client.post(
                "/api/orders/",
                {
                    "customer_name": "Test Buyer",
                    "customer_email": user.email,
                    "customer_phone": "2640000000",
                    "shipping_option": self.shipping_option.id,
                    "payment_method": Order.PaymentMethod.MERCADO_PAGO,
                    "items": [{"variant": self.variant.id, "quantity": 1}],
                },
                format="json",
            )
            results.append(response.status_code)
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            results.append(exc)
        finally:
            connection.close()

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_two_simultaneous_buyers_cannot_take_the_last_unit(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-concurrent",
            "init_point": "https://mp/pref-concurrent",
        }

        barrier = threading.Barrier(2)
        results = []
        threads = [
            threading.Thread(
                target=self._buy_last_unit,
                args=(user, barrier, results),
            )
            for user in (self.user_one, self.user_two)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            [r for r in results if not isinstance(r, int)],
            [],
            "a request raised an exception",
        )
        self.assertEqual(sorted(results), [201, 400])
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)


class OrderCancellationTest(TestCase):
    """R6: cancelling releases the reserved units exactly once."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="cancel-buyer@example.com",
            email="cancel-buyer@example.com",
            password="test-password-123",
        )
        self.other_user = CustomUser.objects.create_user(
            username="other-buyer@example.com",
            email="other-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Omega 3",
            slug="omega-3",
            description="Test product",
            price=Decimal("2000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="60 caps",
            flavor="Unflavored",
            sku="R6-001",
            stock=10,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_order(self):
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

    def _cancel(self, order, client=None):
        client = client or self.client
        return client.post(f"/api/orders/{order.id}/cancel/")

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_cancelling_a_pending_order_releases_stock_once(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-cancel",
            "init_point": "https://mp/pref-cancel",
        }
        order = self._create_order()

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)

        response = self._cancel(order)

        self.assertEqual(response.status_code, 200, response.data)

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_cancelling_twice_releases_stock_only_once(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-cancel-twice",
            "init_point": "https://mp/pref-cancel-twice",
        }
        order = self._create_order()

        self.assertEqual(self._cancel(order).status_code, 200)
        second = self._cancel(order)

        self.assertEqual(second.status_code, 200, second.data)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_cancelling_a_committed_order_does_not_release_stock(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-committed",
            "init_point": "https://mp/pref-committed",
        }
        order = self._create_order()
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.CONFIRMED,
            stock_state=Order.StockState.COMMITTED,
        )

        response = self._cancel(order)

        self.assertEqual(response.status_code, 200, response.data)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_delivered_order_cannot_be_cancelled(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-delivered",
            "init_point": "https://mp/pref-delivered",
        }
        order = self._create_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.DELIVERED)

        response = self._cancel(order)

        self.assertEqual(response.status_code, 400, response.data)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_cannot_cancel_someone_elses_order(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-foreign",
            "init_point": "https://mp/pref-foreign",
        }
        order = self._create_order()

        other_client = APIClient()
        other_client.force_authenticate(user=self.other_user)
        response = self._cancel(order, client=other_client)

        self.assertEqual(response.status_code, 404, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)


class OrderPaymentStateTest(TestCase):
    """Payment transitions must persist both status and stock_state."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="pay-buyer@example.com",
            email="pay-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Magnesium",
            slug="magnesium",
            description="Test product",
            price=Decimal("4000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="120 caps",
            flavor="Unflavored",
            sku="PAY-001",
            stock=10,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_order(self, payment_method=Order.PaymentMethod.CASH):
        response = self.client.post(
            "/api/orders/",
            {
                "customer_name": "Test Buyer",
                "customer_email": self.user.email,
                "customer_phone": "2640000000",
                "shipping_option": self.shipping_option.id,
                "payment_method": payment_method,
                "items": [{"variant": self.variant.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(id=response.data["id"])

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_confirming_a_cash_order_commits_stock(self, mock_send_email):
        order = self._create_order()

        confirm_order_payment(order)

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_rejecting_a_payment_releases_stock(self, mock_send_email):
        order = self._create_order()

        reject_order_payment(order, "payment-1")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_REJECTED)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    @patch("payments.views.mercadopago.SDK")
    def test_webhook_approved_persists_status_and_stock(
        self, mock_sdk_class, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-webhook",
            "init_point": "https://mp/pref-webhook",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)
        mock_sdk_class.return_value.payment.return_value.get.return_value = {
            "status": 200,
            "response": {
                "external_reference": str(order.id),
                "status": "approved",
            },
        }

        process_mp_payment("payment-approved")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    @patch("payments.views.mercadopago.SDK")
    def test_webhook_rejected_persists_status_and_stock(
        self, mock_sdk_class, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-webhook-reject",
            "init_point": "https://mp/pref-webhook-reject",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)
        mock_sdk_class.return_value.payment.return_value.get.return_value = {
            "status": 200,
            "response": {
                "external_reference": str(order.id),
                "status": "rejected",
            },
        }

        process_mp_payment("payment-rejected")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_REJECTED)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_approved_after_rejection_retakes_stock(
        self, mock_create_preference, mock_send_email
    ):
        """The customer retries with another payment method and it succeeds."""
        mock_create_preference.return_value = {
            "id": "pref-retry",
            "init_point": "https://mp/pref-retry",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)

        reject_order_payment(order, "payment-rejected")
        order.refresh_from_db()
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)

        confirm_order_payment(order, "payment-approved-retry")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_approved_after_rejection_without_stock_is_not_confirmed(
        self, mock_create_preference, mock_send_email
    ):
        """A late approval must not confirm an order we cannot fulfill."""
        mock_create_preference.return_value = {
            "id": "pref-retry-nostock",
            "init_point": "https://mp/pref-retry-nostock",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)

        reject_order_payment(order, "payment-rejected")
        Variant.objects.filter(pk=self.variant.pk).update(stock=0)

        confirm_order_payment(order, "payment-approved-retry")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_REJECTED)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 0)
        # The approved payment is recorded so a human can see it and refund.
        self.assertEqual(order.mercadopago_payment_id, "payment-approved-retry")

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_late_rejection_does_not_undo_a_confirmed_sale(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-late-reject",
            "init_point": "https://mp/pref-late-reject",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)

        confirm_order_payment(order, "payment-approved")
        reject_order_payment(order, "payment-stale-rejection")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)


class OrderAdminTest(TestCase):
    """The admin must not be able to bypass the stock transitions."""

    def setUp(self):
        self.staff = CustomUser.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="test-password-123",
        )
        self.user = CustomUser.objects.create_user(
            username="admin-buyer@example.com",
            email="admin-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Creatine",
            slug="creatine",
            description="Test product",
            price=Decimal("3000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="300g",
            flavor="Unflavored",
            sku="ADM-001",
            stock=10,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.admin_client = Client()
        self.admin_client.force_login(self.staff)

    def _create_order(self, payment_method=Order.PaymentMethod.CASH):
        response = self.client.post(
            "/api/orders/",
            {
                "customer_name": "Test Buyer",
                "customer_email": self.user.email,
                "customer_phone": "2640000000",
                "shipping_option": self.shipping_option.id,
                "payment_method": payment_method,
                "items": [{"variant": self.variant.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(id=response.data["id"])

    def _run_action(self, action, order):
        return self.admin_client.post(
            "/admin/orders/order/",
            {"action": action, "_selected_action": [str(order.id)]},
            follow=True,
        )

    def test_status_cannot_be_edited_in_the_change_form(self):
        order = self._create_order()

        response = self.admin_client.get(f"/admin/orders/order/{order.id}/change/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("status", response.context["adminform"].form.fields)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_admin_action_confirms_and_commits_stock(self, mock_send_email):
        order = self._create_order()

        response = self._run_action("mark_as_confirmed", order)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_admin_action_cancels_and_releases_stock(self, mock_send_email):
        order = self._create_order()

        response = self._run_action("mark_as_cancelled", order)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_admin_action_delivers_only_confirmed_orders(self, mock_send_email):
        order = self._create_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CONFIRMED)

        response = self._run_action("mark_as_delivered", order)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_admin_action_does_not_confirm_a_cancelled_order(self, mock_send_email):
        order = self._create_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)

        self._run_action("mark_as_confirmed", order)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_admin_action_reports_orders_it_could_not_confirm(self, mock_send_email):
        order = self._create_order()
        # The reservation was released and the units went to somebody else, so
        # the approval has nothing left to commit.
        release_stock(order)
        Variant.objects.filter(pk=self.variant.pk).update(stock=0)

        response = self._run_action("mark_as_confirmed", order)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertContains(response, "0 pedido(s) confirmado(s)")
        self.assertContains(response, "sin stock o con variante inactiva")
        self.assertContains(response, f"#{order.pk}")

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_admin_action_releases_expired_reservations(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-admin-expire",
            "init_point": "https://mp/pref-admin-expire",
        }
        order = self._create_order(payment_method=Order.PaymentMethod.MERCADO_PAGO)
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        response = self._run_action("mark_as_expired", order)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)


class OrderPaymentMethodRulesTest(TestCase):
    """R4: cash is collected in person, so it requires local pickup."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="cash-buyer@example.com",
            email="cash-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Casein",
            slug="casein",
            description="Test product",
            price=Decimal("5000.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="1kg",
            flavor="Vanilla",
            sku="CASH-001",
            stock=10,
        )
        self.pickup = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.delivery = ShippingOption.objects.create(
            name="Envio local (Uber)",
            type=ShippingOption.Type.LOCAL_DELIVERY,
            price=None,
        )
        self.national = ShippingOption.objects.create(
            name="Envio nacional",
            type=ShippingOption.Type.NATIONAL,
            price=Decimal("8000.00"),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _payload(self, payment_method, shipping_option):
        return {
            "customer_name": "Test Buyer",
            "customer_email": self.user.email,
            "customer_phone": "2640000000",
            "shipping_option": shipping_option.id,
            "payment_method": payment_method,
            "items": [{"variant": self.variant.id, "quantity": 1}],
        }

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_cash_requires_local_pickup(self, mock_send_email):
        response = self.client.post(
            "/api/orders/",
            self._payload(Order.PaymentMethod.CASH, self.delivery),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("payment_method", response.data)
        self.assertEqual(Order.objects.count(), 0)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_cash_with_local_pickup_is_accepted(self, mock_send_email):
        response = self.client.post(
            "/api/orders/",
            self._payload(Order.PaymentMethod.CASH, self.pickup),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_mercado_pago_can_use_local_delivery(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-delivery",
            "init_point": "https://mp/pref-delivery",
        }

        response = self.client.post(
            "/api/orders/",
            self._payload(Order.PaymentMethod.MERCADO_PAGO, self.delivery),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_inactive_shipping_option_is_rejected(self, mock_send_email):
        self.national.is_active = False
        self.national.save(update_fields=["is_active"])

        response = self.client.post(
            "/api/orders/",
            self._payload(Order.PaymentMethod.MERCADO_PAGO, self.national),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("shipping_option", response.data)
        self.assertEqual(Order.objects.count(), 0)


class OrderReservationExpiryTest(TestCase):
    """R3: a payment window that runs out must return the units to stock."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="expiry-buyer@example.com",
            email="expiry-buyer@example.com",
            password="test-password-123",
        )
        self.product = Product.objects.create(
            brand="Brand",
            name="Glutamine",
            slug="glutamine",
            description="Test product",
            price=Decimal("2500.00"),
        )
        self.variant = Variant.objects.create(
            product=self.product,
            size="300g",
            flavor="Unflavored",
            sku="EXP-001",
            stock=10,
        )
        self.shipping_option = ShippingOption.objects.create(
            name="Retiro en local",
            type=ShippingOption.Type.LOCAL_PICKUP,
            price=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_order(self, payment_method=Order.PaymentMethod.MERCADO_PAGO):
        response = self.client.post(
            "/api/orders/",
            {
                "customer_name": "Test Buyer",
                "customer_email": self.user.email,
                "customer_phone": "2640000000",
                "shipping_option": self.shipping_option.id,
                "payment_method": payment_method,
                "items": [{"variant": self.variant.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(id=response.data["id"])

    def _expire(self, order):
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        order.refresh_from_db()

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_expired_reservation_returns_units(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-expire",
            "init_point": "https://mp/pref-expire",
        }
        order = self._create_order()
        self._expire(order)

        released = release_expired_reservations()

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(released, 1)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.expires_at)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_active_reservation_is_untouched(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-active",
            "init_point": "https://mp/pref-active",
        }
        self._create_order()

        released = release_expired_reservations()

        self.variant.refresh_from_db()
        self.assertEqual(released, 0)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    def test_cash_order_never_expires(self, mock_send_email):
        order = self._create_order(payment_method=Order.PaymentMethod.CASH)
        self.assertIsNone(order.expires_at)

        released = release_expired_reservations()

        self.variant.refresh_from_db()
        self.assertEqual(released, 0)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_running_twice_releases_only_once(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-expire-twice",
            "init_point": "https://mp/pref-expire-twice",
        }
        order = self._create_order()
        self._expire(order)

        self.assertEqual(release_expired_reservations(), 1)
        self.assertEqual(release_expired_reservations(), 0)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_committed_order_is_never_released(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-committed-expiry",
            "init_point": "https://mp/pref-committed-expiry",
        }
        order = self._create_order()
        confirm_order_payment(order, "payment-approved")
        self._expire(order)

        released = release_expired_reservations()

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(released, 0)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_command_dry_run_reports_without_releasing(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-dry-run",
            "init_point": "https://mp/pref-dry-run",
        }
        order = self._create_order()
        self._expire(order)
        out = StringIO()

        call_command("expire_stock_reservations", "--dry-run", stdout=out)

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertIn("1 reserva(s) vencida(s)", out.getvalue())
        self.assertEqual(order.stock_state, Order.StockState.RESERVED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_command_releases_expired_reservations(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-command",
            "init_point": "https://mp/pref-command",
        }
        order = self._create_order()
        self._expire(order)
        out = StringIO()

        call_command("expire_stock_reservations", stdout=out)

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertIn("1 reserva(s) liberada(s)", out.getvalue())
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(self.variant.stock, 10)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_late_approval_after_expiry_retakes_stock(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-late-approval",
            "init_point": "https://mp/pref-late-approval",
        }
        order = self._create_order()
        self._expire(order)
        release_expired_reservations()
        order.refresh_from_db()

        confirm_order_payment(order, "payment-late")

        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.stock_state, Order.StockState.COMMITTED)
        self.assertEqual(self.variant.stock, 9)

    @patch("orders.serializers.send_new_order_email_to_admin")
    @patch("orders.serializers.create_payment_preference")
    def test_late_approval_after_expiry_without_stock_is_not_confirmed(
        self, mock_create_preference, mock_send_email
    ):
        mock_create_preference.return_value = {
            "id": "pref-late-approval-nostock",
            "init_point": "https://mp/pref-late-approval-nostock",
        }
        order = self._create_order()
        self._expire(order)
        release_expired_reservations()
        Variant.objects.filter(pk=self.variant.pk).update(stock=0)
        order.refresh_from_db()

        confirm_order_payment(order, "payment-late")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.stock_state, Order.StockState.RELEASED)
        self.assertEqual(order.mercadopago_payment_id, "payment-late")

