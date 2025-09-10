import copy

from django.db import migrations


def forwards(apps, schema_editor):
    AgendaPricing = apps.get_model('pricing', 'AgendaPricing')
    for agenda_pricing in AgendaPricing.objects.all():
        agenda_pricing.agendas.set([agenda_pricing.agenda])


def backwards(apps, schema_editor):
    AgendaPricing = apps.get_model('pricing', 'AgendaPricing')
    for agenda_pricing in AgendaPricing.objects.all():
        if agenda_pricing.agendas.count() == 0:
            agenda_pricing.delete()
        if agenda_pricing.agendas.count() == 1:
            agenda_pricing.agenda = agenda_pricing.agendas.first()
            agenda_pricing.save()
        else:
            for agenda in agenda_pricing.agendas.all():
                new_agenda_pricing = copy.deepcopy(agenda_pricing)
                new_agenda_pricing.pk = None
                new_agenda_pricing.agenda_id = agenda.pk
                new_agenda_pricing.save()
            agenda_pricing.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0005_agenda_pricing_m2m'),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=backwards),
    ]
