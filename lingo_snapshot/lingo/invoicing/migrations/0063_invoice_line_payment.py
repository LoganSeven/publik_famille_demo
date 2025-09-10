import decimal

from django.db import migrations


def forward(apps, schema_editor):
    InvoicePayment = apps.get_model('invoicing', 'InvoicePayment')
    InvoiceLinePayment = apps.get_model('invoicing', 'InvoiceLinePayment')
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')

    for invoice_payment in InvoicePayment.objects.all():
        amount_to_assign = invoice_payment.amount
        for line in InvoiceLine.objects.filter(invoice=invoice_payment.invoice).order_by('pk'):
            # trigger not played yet, remaining_amount is not up to date
            line.remaining_amount = line.total_amount - line.paid_amount
            if not line.remaining_amount:
                # nothing to pay for this line
                continue
            # paid_amount for this line: it can not be greater than line remaining_amount
            paid_amount = decimal.Decimal(min(line.remaining_amount, amount_to_assign))
            # create payment for the line
            InvoiceLinePayment.objects.create(
                payment=invoice_payment.payment,
                line=line,
                amount=paid_amount,
            )
            # new amount to assign
            amount_to_assign -= paid_amount
            if amount_to_assign <= 0:
                break


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0062_invoice_line_payment'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
