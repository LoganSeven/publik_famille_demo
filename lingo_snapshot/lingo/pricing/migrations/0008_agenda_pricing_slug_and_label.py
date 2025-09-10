from django.db import migrations
from django.utils.text import slugify

from lingo.utils.misc import generate_slug


def forwards(apps, schema_editor):
    AgendaPricing = apps.get_model('pricing', 'AgendaPricing')
    for agenda_pricing in AgendaPricing.objects.all():
        agenda_pricing.label = agenda_pricing.pricing.label
        agenda_pricing.base_slug = slugify(agenda_pricing.label)
        agenda_pricing.slug = generate_slug(agenda_pricing)
        agenda_pricing.save()


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0007_agenda_pricing_slug_and_label'),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop),
    ]
