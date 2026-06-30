from django.urls import include, path
from rest_framework.routers import DefaultRouter

from shipping.views import ShippingViewSet

router = DefaultRouter()
router.register("shipping", ShippingViewSet)

urlpatterns = [path("", include(router.urls))]
