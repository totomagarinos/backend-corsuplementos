from django.urls import path
from payments.views import MercadoPagoWebhookView

urlpatterns = [
    path("webhook/", MercadoPagoWebhookView.as_view(), name="mercadopago-webhook"),
]
