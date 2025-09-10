from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0013_formatted_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceline',
            name='error_status',
            field=models.CharField(
                blank=True, choices=[('ignored', 'Ignored'), ('fixed', 'Fixed')], max_length=10
            ),
        ),
    ]
