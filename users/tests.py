from django.test import TestCase

from users.models import CustomUser


class CustomUserModelTest(TestCase):
    def test_new_user_is_not_vip_by_default(self):
        user = CustomUser.objects.create_user(
            username="user@example.com",
            email="user@example.com",
            password="test-password-123",
        )

        self.assertFalse(user.is_vip)
