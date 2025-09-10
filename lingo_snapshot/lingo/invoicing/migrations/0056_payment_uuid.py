import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0055_payment_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payment',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
