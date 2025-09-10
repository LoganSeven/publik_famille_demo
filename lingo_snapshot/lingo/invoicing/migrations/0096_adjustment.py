from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0095_credit_auto_assign'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='adjustment_campaign',
            field=models.BooleanField(default=False, verbose_name='Adjustment campaign'),
        ),
    ]
