import django.core.serializers.json
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0044_total_amount'),
    ]

    operations = [
        migrations.CreateModel(
            name='JournalLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('event_date', models.DateField()),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('user_first_name', models.CharField(max_length=250)),
                ('user_last_name', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
                ('payer_first_name', models.CharField(max_length=250)),
                ('payer_last_name', models.CharField(max_length=250)),
                ('payer_demat', models.BooleanField(default=False)),
                ('payer_direct_debit', models.BooleanField(default=False)),
                ('event', models.JSONField(default=dict)),
                (
                    'pricing_data',
                    models.JSONField(default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[('success', 'Success'), ('warning', 'Warning'), ('error', 'Error')],
                        max_length=10,
                    ),
                ),
                (
                    'error_status',
                    models.CharField(
                        blank=True, choices=[('ignored', 'Ignored'), ('fixed', 'Fixed')], max_length=10
                    ),
                ),
                (
                    'from_injected_line',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.injectedline'
                    ),
                ),
                (
                    'invoice_line',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='journal_lines',
                        to='invoicing.invoiceline',
                    ),
                ),
                (
                    'pool',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='DraftJournalLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('event_date', models.DateField()),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('user_first_name', models.CharField(max_length=250)),
                ('user_last_name', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
                ('payer_first_name', models.CharField(max_length=250)),
                ('payer_last_name', models.CharField(max_length=250)),
                ('payer_demat', models.BooleanField(default=False)),
                ('payer_direct_debit', models.BooleanField(default=False)),
                ('event', models.JSONField(default=dict)),
                (
                    'pricing_data',
                    models.JSONField(default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[('success', 'Success'), ('warning', 'Warning'), ('error', 'Error')],
                        max_length=10,
                    ),
                ),
                (
                    'error_status',
                    models.CharField(
                        blank=True, choices=[('ignored', 'Ignored'), ('fixed', 'Fixed')], max_length=10
                    ),
                ),
                (
                    'from_injected_line',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.injectedline'
                    ),
                ),
                (
                    'invoice_line',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='journal_lines',
                        to='invoicing.draftinvoiceline',
                    ),
                ),
                (
                    'pool',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
