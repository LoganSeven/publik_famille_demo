import django.db.migrations.operations.special
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    replaces = [
        ('pricing', '0001_initial'),
        ('pricing', '0002_pricing'),
        ('pricing', '0003_extra_variables'),
        ('pricing', '0004_criteria_default'),
        ('pricing', '0005_agenda_pricing_m2m'),
        ('pricing', '0006_agenda_pricing_m2m'),
        ('pricing', '0007_agenda_pricing_slug_and_label'),
        ('pricing', '0008_agenda_pricing_slug_and_label'),
        ('pricing', '0009_agenda_pricing_m2m'),
        ('pricing', '0010_flat_fee_schedule'),
        ('pricing', '0011_payer_variables'),
        ('pricing', '0012_payer'),
        ('pricing', '0013_reduction_rate'),
        ('pricing', '0014_effort_rate'),
        ('pricing', '0015_effort_rate'),
        ('pricing', '0016_merge_models'),
        ('pricing', '0017_merge_models'),
        ('pricing', '0018_merge_models'),
        ('pricing', '0019_merge_models'),
    ]

    initial = True

    dependencies = [
        ('agendas', '0003_check_type_group'),
    ]

    operations = [
        migrations.CreateModel(
            name='Criteria',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, verbose_name='Identifier')),
                ('condition', models.CharField(blank=True, max_length=1000, verbose_name='Condition')),
                ('order', models.PositiveIntegerField()),
                (
                    'default',
                    models.BooleanField(
                        default=False,
                        help_text='Will be applied if no other criteria matches',
                        verbose_name='Default criteria',
                    ),
                ),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.CreateModel(
            name='CriteriaCategory',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.CreateModel(
            name='Pricing',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                (
                    'label',
                    models.CharField(max_length=150, null=True, verbose_name='Label'),
                ),
                (
                    'slug',
                    models.SlugField(max_length=160, null=True, verbose_name='Identifier'),
                ),
                (
                    'flat_fee_schedule',
                    models.BooleanField(default=False, verbose_name='Flat fee schedule'),
                ),
                (
                    'subscription_required',
                    models.BooleanField(default=True, verbose_name='Subscription is required'),
                ),
                (
                    'extra_variables',
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    'kind',
                    models.CharField(
                        choices=[
                            ('basic', 'Basic'),
                            ('reduction', 'Reduction rate'),
                            ('effort', 'Effort rate'),
                        ],
                        default='basic',
                        max_length=10,
                        verbose_name='Kind of pricing',
                    ),
                ),
                (
                    'min_pricing',
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=9, null=True, verbose_name='Minimal pricing'
                    ),
                ),
                (
                    'max_pricing',
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=9, null=True, verbose_name='Maximal pricing'
                    ),
                ),
                (
                    'reduction_rate',
                    models.CharField(
                        blank=True,
                        help_text='The result is expressed as a percentage, and must be between 0 and 100.',
                        max_length=1000,
                        verbose_name='Reduction rate (template)',
                    ),
                ),
                (
                    'effort_rate_target',
                    models.CharField(
                        blank=True,
                        help_text='The result is expressed as an amount, which is then multiplied by the effort rate.',
                        max_length=1000,
                        verbose_name='Amount to be multiplied by the effort rate (template)',
                    ),
                ),
                ('date_start', models.DateField(verbose_name='Start date')),
                ('date_end', models.DateField(verbose_name='End date')),
                ('pricing_data', models.JSONField(null=True)),
                ('agendas', models.ManyToManyField(related_name='pricings', to='agendas.Agenda')),
            ],
        ),
        migrations.CreateModel(
            name='PricingCriteriaCategory',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('order', models.PositiveIntegerField()),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='pricing.criteriacategory'
                    ),
                ),
                (
                    'pricing',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='pricing.pricing'),
                ),
            ],
            options={
                'ordering': ['order'],
                'unique_together': {('pricing', 'category')},
            },
        ),
        migrations.AddField(
            model_name='pricing',
            name='categories',
            field=models.ManyToManyField(
                related_name='pricings',
                through='pricing.PricingCriteriaCategory',
                to='pricing.CriteriaCategory',
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='criterias',
            field=models.ManyToManyField(to='pricing.Criteria'),
        ),
        migrations.AddField(
            model_name='criteria',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='criterias',
                to='pricing.criteriacategory',
                verbose_name='Category',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='criteria',
            unique_together={('category', 'slug')},
        ),
        migrations.AlterModelOptions(
            name='criteria',
            options={'ordering': ['default', 'order']},
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
                    'pricing',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='billingdates',
                        to='pricing.pricing',
                    ),
                ),
            ],
        ),
    ]
