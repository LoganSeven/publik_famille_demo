from django.db import migrations, models, transaction
from django.db.models import Count


def dedup(apps, schema_editor):
    APIClient = apps.get_model('authentic2', 'APIClient')
    max_length = APIClient.identifier.field.max_length

    identifiers_count = APIClient.objects.values('identifier').annotate(num=Count('identifier'))
    clients = (
        APIClient.objects.select_for_update()
        .annotate(
            num=models.Subquery(
                identifiers_count.filter(identifier=models.OuterRef('identifier')).values('num')
            )
        )
        .order_by('id')
    )

    with transaction.atomic():
        duplicated_id = clients.filter(num__gte=2).values_list('identifier', flat=True)
        identifiers = set(clients.values_list('identifier', flat=True))
        for duplicate in duplicated_id:
            suffix = 1
            for client in clients.all().filter(identifier=duplicate).order_by('id')[1:]:
                while True:
                    suffix += 1
                    suffix_str = '_%d' % suffix
                    new_identifier = '%s%s' % (duplicate[: max_length - len(suffix_str)], suffix_str)
                    if new_identifier not in identifiers:
                        identifiers |= {new_identifier}
                        break
                client.identifier_legacy = client.identifier
                client.identifier = new_identifier
                client.save()


def redup(apps, schema_editor):
    APIClient = apps.get_model('authentic2', 'APIClient')
    for client in APIClient.objects.filter(identifier_legacy__isnull=False).all():
        client.identifier = client.identifier_legacy
        client.identifier_legacy = None
        client.save()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0058_apiclient_identifier_legacy'),
    ]

    operations = [
        migrations.RunPython(dedup, redup),
    ]
