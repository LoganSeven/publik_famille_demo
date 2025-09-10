import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0069_line_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='draftinvoiceline',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
    ]
