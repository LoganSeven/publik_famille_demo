from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0010_item_event_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='basketline',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='basketlineitem',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
