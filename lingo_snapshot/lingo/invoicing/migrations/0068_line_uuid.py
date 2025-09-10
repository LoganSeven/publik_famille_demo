import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0067_campaign_custom_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoiceline',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
    ]
