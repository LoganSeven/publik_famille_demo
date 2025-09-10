from django.db import migrations


def forward(apps, schema_editor):
    Pool = apps.get_model('invoicing', 'Pool')
    for pool in Pool.objects.all():
        pool.status = 'completed'
        pool.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0005_pool_status'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
