from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0012_counter'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='formatted_number',
            field=models.CharField(default='', max_length=200),
            preserve_default=False,
        ),
    ]
