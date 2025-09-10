import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0007_negative_pricing_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='checktypegroup',
            name='unjustified_absence',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='agendas.checktype',
                verbose_name='Check type to be used in case of unjustified absence',
            ),
        ),
        migrations.AlterField(
            model_name='checktypegroup',
            name='unexpected_presence',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='agendas.checktype',
                verbose_name='Check type to be used in case of unexpected presence',
            ),
        ),
    ]
