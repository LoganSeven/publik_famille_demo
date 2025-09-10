import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0004_basket_validated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='expiry_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.RemoveField(
            model_name='basket',
            name='updated_at',
        ),
    ]
