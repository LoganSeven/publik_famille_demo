from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0008_activity_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='basketlineitem',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
    ]
