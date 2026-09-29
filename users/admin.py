from django.contrib import admin
from .models import CustomUser
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth import get_user_model

User = get_user_model()

try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = [
        "username",
        "email",
        "is_vip",
        "is_staff",
        "is_active",
    ]
    list_filter = ["is_vip", "is_staff", "is_active"]
    search_fields = ("email", "username")

    fieldsets = UserAdmin.fieldsets + (("Beneficios", {"fields": ("is_vip",)}),)

    add_fieldsets = UserAdmin.add_fieldsets + (("Beneficios", {"fields": ("is_vip",)}),)
