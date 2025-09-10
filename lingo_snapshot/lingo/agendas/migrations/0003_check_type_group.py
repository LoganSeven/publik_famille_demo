import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0002_agenda'),
    ]

    operations = [
        migrations.AddField(
            model_name='agenda',
            name='check_type_group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='agendas.CheckTypeGroup',
                verbose_name='Check type group',
            ),
        ),
    ]
