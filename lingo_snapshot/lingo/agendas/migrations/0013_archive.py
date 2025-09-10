from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agendas', '0012_unlock_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='agenda',
            name='archived',
            field=models.BooleanField(default=False),
        ),
    ]
