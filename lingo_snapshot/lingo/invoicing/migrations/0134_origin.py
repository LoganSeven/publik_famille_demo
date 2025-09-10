from django.db import migrations


def forward(apps, schema_editor):
    Invoice = apps.get_model('invoicing', 'Invoice')
    DraftInvoice = apps.get_model('invoicing', 'DraftInvoice')
    Credit = apps.get_model('invoicing', 'Credit')

    for klass in [Invoice, DraftInvoice, Credit]:
        klass.objects.filter(origin__isnull=True, pool__isnull=False).update(origin='campaign')
        klass.objects.filter(origin__isnull=True, basket__isnull=False).update(origin='basket')
        klass.objects.filter(origin__isnull=True).update(origin='api')


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0133_origin'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
