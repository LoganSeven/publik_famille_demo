import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0001_initial'),
        ('agendas', '0003_check_type_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='agenda',
            name='regie',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='invoicing.Regie',
                verbose_name='Regie',
            ),
        ),
    ]
