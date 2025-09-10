import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0005_partial_bookings'),
    ]

    operations = [
        migrations.AddField(
            model_name='checktypegroup',
            name='unexpected_presence',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='agendas.checktype',
                verbose_name='Check type to be used in case of unexpected presence',
            ),
        ),
    ]
