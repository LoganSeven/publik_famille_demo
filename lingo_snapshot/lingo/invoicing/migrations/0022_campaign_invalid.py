from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0021_campaign_agendas'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='invalid',
            field=models.BooleanField(default=False),
        ),
    ]
