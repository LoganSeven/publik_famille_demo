from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0004_campaign'),
    ]

    operations = [
        migrations.AddField(
            model_name='pool',
            name='completed_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='pool',
            name='status',
            field=models.CharField(
                choices=[
                    ('registered', 'Registered'),
                    ('running', 'Running'),
                    ('failed', 'Failed'),
                    ('completed', 'Completed'),
                ],
                default='registered',
                max_length=100,
            ),
        ),
    ]
