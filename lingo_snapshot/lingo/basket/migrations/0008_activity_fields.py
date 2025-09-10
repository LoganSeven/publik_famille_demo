from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0007_information_messages'),
    ]

    operations = [
        migrations.AddField(
            model_name='basketlineitem',
            name='activity_label',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='basketlineitem',
            name='agenda_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='basketlineitem',
            name='event_label',
            field=models.CharField(default='', max_length=260),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='basketlineitem',
            name='event_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
