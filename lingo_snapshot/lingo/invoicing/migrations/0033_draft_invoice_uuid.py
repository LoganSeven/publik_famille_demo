import uuid

from django.db import migrations


def forward(apps, schema_editor):
    DraftInvoice = apps.get_model('invoicing', 'DraftInvoice')
    for invoice in DraftInvoice.objects.all():
        invoice.uuid = uuid.uuid4()
        invoice.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0032_draft_invoice_uuid'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
