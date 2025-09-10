from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0059_quantity'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftjournalline',
            name='booking',
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name='journalline',
            name='booking',
            field=models.JSONField(default=dict),
        ),
    ]
