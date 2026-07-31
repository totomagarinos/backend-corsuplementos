from django.contrib import admin

from products.models import Product, Variant


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0


class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "brand",
        "category",
        "base_price",
        "is_active",
        "created_at",
    ]
    list_editable = ["is_active"]
    list_filter = ["category", "is_active"]
    inlines = [VariantInline]
    readonly_fields = ["created_at", "updated_at"]


admin.site.register(Product, ProductAdmin)
