from rest_framework import viewsets, mixins

from orders.models import Order
from orders.serializers import OrderSerializer


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = OrderSerializer

    def get_queryset(self):
        session_id = self.request.headers.get("X-Session-ID")
        if session_id:
            return Order.objects.filter(session_id=session_id)
        return Order.objects.none()
