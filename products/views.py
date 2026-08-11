from django.db.models import Q
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

        search_query = self.request.query_params.get("q", None)

        if search_query:
            words = search_query.split()

            q_objects = Q()

            for word in words:
                q_objects |= (
                    Q(name__icontains=word)
                    | Q(category__icontains=word)
                    | Q(brand__icontains=word)
                    | Q(description__icontains=word)
                )

            queryset = queryset.filter(q_objects).distinct()

        return queryset
