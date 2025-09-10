from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0002_pricing'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='extra_variables',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
