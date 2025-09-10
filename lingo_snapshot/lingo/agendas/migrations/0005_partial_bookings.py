from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0004_regie'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='agenda',
            options={'ordering': ['label']},
        ),
        migrations.AddField(
            model_name='agenda',
            name='partial_bookings',
            field=models.BooleanField(default=False),
        ),
    ]
