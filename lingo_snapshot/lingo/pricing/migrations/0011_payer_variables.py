from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0010_flat_fee_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='payer_variables',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
