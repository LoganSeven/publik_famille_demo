import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0003_check_type_group'),
        ('pricing', '0004_criteria_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendapricing',
            name='agendas',
            field=models.ManyToManyField(to='agendas.Agenda', related_name='agendapricings'),
        ),
        migrations.AlterField(
            model_name='agendapricing',
            name='agenda',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='old_agendapricings',
                to='agendas.Agenda',
            ),
        ),
    ]
