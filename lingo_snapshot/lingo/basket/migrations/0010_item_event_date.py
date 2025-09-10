import datetime

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0009_accounting_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='basketlineitem',
            name='event_date',
            field=models.DateField(default=datetime.date.today),
            preserve_default=False,
        ),
    ]
