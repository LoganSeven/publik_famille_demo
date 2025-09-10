import uuid

from django.db import migrations
from django.db.models import Count


def forward(apps, schema_editor):
    InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')
    DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
    CreditLine = apps.get_model('invoicing', 'CreditLine')
    for model in [InvoiceLine, DraftInvoiceLine, CreditLine]:
        duplicates = model.objects.values('uuid').annotate(Count('uuid')).order_by().filter(uuid__count__gt=1)
        for values in duplicates:
            for line in model.objects.filter(uuid=values['uuid']).order_by('pk')[1:]:
                line.uuid = uuid.uuid4()
                line.save()
        for line in model.objects.filter(uuid__isnull=True):
            line.uuid = uuid.uuid4()
            line.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0100_snapshot_models'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
