from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0070_line_uuid'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='cashier_name',
            field=models.CharField(blank=True, max_length=256, verbose_name='Cashier name'),
        ),
        migrations.AddField(
            model_name='regie',
            name='city_name',
            field=models.CharField(blank=True, max_length=256, verbose_name='City name'),
        ),
    ]
