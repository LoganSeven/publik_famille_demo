from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0006_pool_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='pool',
            name='exception',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
    ]
