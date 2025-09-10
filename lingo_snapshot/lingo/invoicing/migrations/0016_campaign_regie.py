from django.db import migrations


def forward(apps, schema_editor):
    Campaign = apps.get_model('invoicing', 'Campaign')
    Pool = apps.get_model('invoicing', 'Pool')
    DraftInvoice = apps.get_model('invoicing', 'DraftInvoice')
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    Invoice = apps.get_model('invoicing', 'Invoice')
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')

    InvoiceLine.objects.all().delete()
    Invoice.objects.all().delete()
    DraftInvoiceLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0015_injected_lines'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
