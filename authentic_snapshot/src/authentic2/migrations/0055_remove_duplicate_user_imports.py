import logging

from django.db import migrations
from django.db.models import Count


def remove_duplicate_imports(apps, schema_editor):
    UserImport = apps.get_model('authentic2', 'UserImport')

    try:
        # get all uuids with duplicate imports
        qs = (
            UserImport.objects.values('uuid')
            .annotate(Count('uuid'))
            .order_by('uuid')
            .filter(uuid__count__gt=1)
        )
        uuids = [v['uuid'] for v in qs]

        to_delete = []

        # get all object ids by uuid except the last created
        for uuid in uuids:
            to_delete.extend(
                [v['id'] for v in UserImport.objects.filter(uuid=uuid).values('id').order_by('-created')[1:]]
            )

        # delete all at once
        UserImport.objects.filter(id__in=to_delete).delete()
    except Exception:
        logging.exception('removing UserImport duplication failed')


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0054_migrate_user_imports'),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_imports, migrations.RunPython.noop),
    ]
