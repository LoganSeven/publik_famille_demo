from django.db import migrations, models
from django.utils.timezone import now


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0009_user_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoiceline',
            name='event_date',
            field=models.DateField(default=now().date),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='event_date',
            field=models.DateField(default=now().date),
            preserve_default=False,
        ),
    ]
