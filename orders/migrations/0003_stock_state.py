from django.db import migrations, models


def backfill_stock_state(apps, schema_editor):
    """Legacy orders only ever deducted at confirmation, so:
    stock_deducted=True  -> the units were already taken  -> committed
    stock_deducted=False -> stock was never touched       -> none
    """
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(stock_deducted=True).update(stock_state="committed")
    Order.objects.filter(stock_deducted=False).update(stock_state="none")


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="stock_state",
            field=models.CharField(
                choices=[
                    ("none", "Sin stock comprometido"),
                    ("reserved", "Reservado"),
                    ("committed", "Comprometido"),
                    ("released", "Liberado"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_stock_state, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="order",
            name="stock_deducted",
        ),
    ]
