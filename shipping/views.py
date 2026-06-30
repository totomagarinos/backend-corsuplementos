from rest_framework import viewsets

from shipping.models import ShippingOption
from shipping.serializers import ShippingSerializer


class ShippingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ShippingOption.objects.filter(is_active=True)
    serializer_class = ShippingSerializer
