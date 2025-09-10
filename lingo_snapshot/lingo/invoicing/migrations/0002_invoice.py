import django.core.serializers.json
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=300, verbose_name='Label')),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9, default=0)),
                ('date_issue', models.DateField(verbose_name='Issue date')),
                ('payer', models.CharField(max_length=300, verbose_name='Payer')),
                (
                    'regie',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='invoicing.Regie',
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='InvoiceLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('quantity', models.FloatField()),
                ('unit_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
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
                    'invoice',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='invoicing.Invoice',
                        related_name='lines',
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='DraftInvoice',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=300, verbose_name='Label')),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9, default=0)),
                ('date_issue', models.DateField(verbose_name='Issue date')),
                ('payer', models.CharField(max_length=300, verbose_name='Payer')),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.Regie'),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='DraftInvoiceLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('quantity', models.FloatField()),
                ('unit_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
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
                    'invoice',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='invoicing.DraftInvoice',
                        related_name='lines',
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
