import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0009_agenda_pricing_m2m'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendapricing',
            name='flat_fee_schedule',
            field=models.BooleanField(default=False, verbose_name='Flat fee schedule'),
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='subscription_required',
            field=models.BooleanField(default=True, verbose_name='Subscription is required'),
        ),
        migrations.AlterField(
            model_name='agendapricing',
            name='date_end',
            field=models.DateField(verbose_name='End date'),
        ),
        migrations.AlterField(
            model_name='agendapricing',
            name='date_start',
            field=models.DateField(verbose_name='Start date'),
        ),
        migrations.AlterField(
            model_name='agendapricing',
            name='pricing',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to='pricing.Pricing',
                verbose_name='Pricing model',
            ),
        ),
        migrations.CreateModel(
            name='BillingDate',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('date_start', models.DateField(verbose_name='Billing start date')),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                (
                    'agenda_pricing',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to='pricing.AgendaPricing',
                        related_name='billingdates',
                    ),
                ),
            ],
        ),
    ]
