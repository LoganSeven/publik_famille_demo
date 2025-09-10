import uuid

from django.db import migrations


def forward(apps, schema_editor):
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    for line in InvoiceLine.objects.all():
        line.uuid = uuid.uuid4()
        line.save()
    for line in DraftInvoiceLine.objects.all():
        line.uuid = uuid.uuid4()
        line.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0068_line_uuid'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
