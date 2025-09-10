from django.db import migrations


def forward(apps, schema_editor):
    Pricing = apps.get_model('pricing', 'Pricing')
    for pricing in Pricing.objects.all():
        if pricing.reduction_rate:
            pricing.kind = 'reduction'
            pricing.save()


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0014_effort_rate'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
