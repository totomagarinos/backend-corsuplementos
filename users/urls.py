from django.urls import path

from users.serializers import EmailTokenObtainSerializer
from users.views import RegisterView

from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path(
        "login/",
        TokenObtainPairView.as_view(serializer_class=EmailTokenObtainSerializer),
        name="login",
    ),
    path("refresh/", TokenRefreshView.as_view(), name="refresh"),
]
