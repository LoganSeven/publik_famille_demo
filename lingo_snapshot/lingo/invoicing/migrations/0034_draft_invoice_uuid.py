import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0033_draft_invoice_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='draftinvoice',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
