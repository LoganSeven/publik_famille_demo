import uuid

from django.db import migrations


def forward(apps, schema_editor):
    Payment = apps.get_model('invoicing', 'Payment')
    for payment in Payment.objects.all():
        payment.uuid = uuid.uuid4()
        payment.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0054_payment_uuid'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
