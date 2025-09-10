from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0065_payer_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='invoice_model',
            field=models.CharField(
                choices=[('basic', 'Basic'), ('middle', 'Middle'), ('full', 'Full')],
                default='middle',
                max_length=10,
                verbose_name='Invoice model',
            ),
        ),
        migrations.AddField(
            model_name='regie',
            name='invoice_model',
            field=models.CharField(
                choices=[('basic', 'Basic'), ('middle', 'Middle'), ('full', 'Full')],
                default='middle',
                max_length=10,
                verbose_name='Invoice model',
            ),
        ),
    ]
