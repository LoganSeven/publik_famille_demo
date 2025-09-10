from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0006_unexpected_presence'),
    ]

    operations = [
        migrations.AlterField(
            model_name='checktype',
            name='pricing_rate',
            field=models.IntegerField(
                blank=True, help_text='Percentage rate', null=True, verbose_name='Pricing rate'
            ),
        ),
    ]
