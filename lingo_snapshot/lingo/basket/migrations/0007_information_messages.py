from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0006_credit'),
    ]

    operations = [
        migrations.RenameField(
            model_name='basketline',
            old_name='information_message',
            new_name='cancel_information_message',
        ),
        migrations.AddField(
            model_name='basketline',
            name='information_message',
            field=models.TextField(blank=True),
        ),
    ]
