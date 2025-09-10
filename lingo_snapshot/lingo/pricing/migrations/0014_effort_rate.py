from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0013_reduction_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='kind',
            field=models.CharField(
                choices=[('basic', 'Basic'), ('reduction', 'Reduction rate'), ('effort', 'Effort rate')],
                default='basic',
                max_length=10,
                verbose_name='Kind of pricing',
            ),
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='max_pricing',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=9, null=True, verbose_name='Maximal pricing'
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='effort_rate_target',
            field=models.CharField(
                blank=True,
                help_text='The result is expressed as an amount, which is then multiplied by the effort rate.',
                max_length=1000,
                verbose_name='Amount to be multiplied by the effort rate (template)',
            ),
        ),
    ]
