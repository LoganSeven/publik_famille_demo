from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0023_campaign_dates'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='finalized',
            field=models.BooleanField(default=False),
        ),
    ]
