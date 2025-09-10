from django.db import migrations


def forward(apps, schema_editor):
    DraftJournalLine = apps.get_model('invoicing', 'DraftJournalLine')
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    JournalLine = apps.get_model('invoicing', 'JournalLine')
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')

    for line_model, journal_line_model in [(DraftInvoiceLine, DraftJournalLine), (InvoiceLine, JournalLine)]:
        for line in line_model.objects.all():
            journal_line_model.objects.create(
                event_date=line.event_date,
                slug=line.slug,
                label=line.label,
                amount=line.total_amount,
                user_external_id=line.user_external_id,
                user_first_name=line.user_first_name,
                user_last_name=line.user_last_name,
                payer_external_id=line.payer_external_id,
                payer_first_name=line.payer_first_name,
                payer_last_name=line.payer_last_name,
                payer_demat=line.payer_demat,
                payer_direct_debit=line.payer_direct_debit,
                event=line.event,
                pricing_data=line.pricing_data,
                status=line.status,
                error_status=getattr(line, 'error_status', ''),
                pool=line.pool,
                from_injected_line=line.from_injected_line,
                invoice_line=line,
            )


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0045_journal_lines'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
