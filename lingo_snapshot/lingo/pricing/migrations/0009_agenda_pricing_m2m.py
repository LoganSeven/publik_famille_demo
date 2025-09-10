from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0008_agenda_pricing_slug_and_label'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='agendapricing',
            name='agenda',
        ),
    ]
