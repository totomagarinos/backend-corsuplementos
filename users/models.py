from django.db import models
from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    is_vip = models.BooleanField(default=False)
