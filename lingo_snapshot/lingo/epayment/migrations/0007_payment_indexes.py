from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('epayment', '0006_date_fields'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='transaction',
            index=models.Index(
                models.F('status'),
                models.OrderBy(models.F('start_date'), descending=True),
                name='transaction_status_date_idx',
            ),
        ),
    ]
