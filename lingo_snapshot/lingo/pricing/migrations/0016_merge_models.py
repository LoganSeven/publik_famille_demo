import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0015_effort_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendapricing',
            name='criterias',
            field=models.ManyToManyField(to='pricing.Criteria'),
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='effort_rate_target',
            field=models.CharField(
                blank=True,
                help_text='The result is expressed as an amount, which is then multiplied by the effort rate.',
                max_length=1000,
                verbose_name='Amount to be multiplied by the effort rate (template)',
            ),
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='extra_variables',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='agendapricing',
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
            name='reduction_rate',
            field=models.CharField(
                blank=True,
                help_text='The result is expressed as a percentage, and must be between 0 and 100.',
                max_length=1000,
                verbose_name='Reduction rate (template)',
            ),
        ),
        migrations.CreateModel(
            name='AgendaPricingCriteriaCategory',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('order', models.PositiveIntegerField()),
                (
                    'agenda_pricing',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='pricing.agendapricing'
                    ),
                ),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='pricing.criteriacategory'
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='categories',
            field=models.ManyToManyField(
                related_name='agendapricings',
                through='pricing.AgendaPricingCriteriaCategory',
                to='pricing.CriteriaCategory',
            ),
        ),
    ]
