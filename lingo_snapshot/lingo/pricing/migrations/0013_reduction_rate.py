from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0012_payer'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendapricing',
            name='min_pricing',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=9, null=True, verbose_name='Minimal pricing'
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='reduction_rate',
            field=models.CharField(
                blank=True,
                help_text=('The result is expressed as a percentage, and must be between 0 and 100.'),
                max_length=1000,
                verbose_name='Reduction rate (template)',
            ),
        ),
    ]
