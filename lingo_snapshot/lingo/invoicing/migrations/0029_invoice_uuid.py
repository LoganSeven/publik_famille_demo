import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0028_invoice_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoice',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
