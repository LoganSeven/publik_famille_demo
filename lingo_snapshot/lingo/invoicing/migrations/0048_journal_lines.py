from django.db import migrations


def forward(apps, schema_editor):
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    DraftJournalLine = apps.get_model('invoicing', 'DraftJournalLine')
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')
    JournalLine = apps.get_model('invoicing', 'JournalLine')

    # delete invoice lines with status != 'success'
    DraftJournalLine.objects.exclude(status='success').update(invoice_line=None)
    DraftInvoiceLine.objects.exclude(status='success').delete()
    JournalLine.objects.exclude(status='success').update(invoice_line=None)
    InvoiceLine.objects.exclude(status='success').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0047_journal_lines'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
