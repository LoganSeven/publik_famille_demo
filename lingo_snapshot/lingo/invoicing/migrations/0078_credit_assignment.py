from django.db import migrations


def forward(apps, schema_editor):
    CreditAssignment = apps.get_model('invoicing', 'CreditAssignment')
    InvoiceLinePayment = apps.get_model('invoicing', 'InvoiceLinePayment')
    for ca in CreditAssignment.objects.all():
        ca.invoice = InvoiceLinePayment.objects.filter(payment=ca.payment).first().line.invoice
        ca.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0077_credit_assignment'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
