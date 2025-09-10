from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0002_accounting_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='min_pricing_data',
            field=models.JSONField(null=True),
        ),
    ]
