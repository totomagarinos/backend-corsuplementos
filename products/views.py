from rest_framework import viewsets

from products.models import Product
from products.serializers import ProductSerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductSerializer
    lookup_field = "slug"

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True).prefetch_related("variants")

        category = self.request.query_params.get("category", None)

        if category is not None:
            queryset = queryset.filter(category=category)

        return queryset
