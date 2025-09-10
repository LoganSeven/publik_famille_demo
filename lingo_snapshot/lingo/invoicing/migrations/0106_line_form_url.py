from django.db import migrations


def forward(apps, schema_editor):
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    CreditLine = apps.get_model('invoicing', 'CreditLine')
    Basket = apps.get_model('basket', 'Basket')

    for basket in Basket.objects.all():
        for line in basket.basketline_set.all():
            if not line.form_url:
                continue
            # propagate basket line form_url to related invoice/credit lines
            DraftInvoiceLine.objects.filter(
                invoice__basket=basket, user_external_id=line.user_external_id
            ).update(form_url=line.form_url)
            InvoiceLine.objects.filter(invoice__basket=basket, user_external_id=line.user_external_id).update(
                form_url=line.form_url
            )
            CreditLine.objects.filter(credit__basket=basket, user_external_id=line.user_external_id).update(
                form_url=line.form_url
            )


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0009_accounting_code'),
        ('invoicing', '0105_line_form_url'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
