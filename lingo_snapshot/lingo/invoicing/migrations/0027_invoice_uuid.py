from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0026_payments'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='uuid',
            field=models.UUIDField(null=True, editable=False),
        ),
    ]
