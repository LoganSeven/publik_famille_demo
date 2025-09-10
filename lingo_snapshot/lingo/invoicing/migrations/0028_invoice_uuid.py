import uuid

from django.db import migrations


def forward(apps, schema_editor):
    Invoice = apps.get_model('invoicing', 'Invoice')
    for invoice in Invoice.objects.all():
        invoice.uuid = uuid.uuid4()
        invoice.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0027_invoice_uuid'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
