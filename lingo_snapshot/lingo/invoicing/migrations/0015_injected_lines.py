from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0014_error_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='injected_lines',
            field=models.CharField(
                choices=[
                    ('no', 'no'),
                    ('period', 'yes, only for the period'),
                    ('all', 'yes, all injected lines before the end of the period'),
                ],
                default='no',
                max_length=10,
                verbose_name='Integrate injected lines',
            ),
        ),
    ]
