import os
import uuid

import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models

with open(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        '..',
        'sql',
        'credit_triggers_for_amount.sql',
    )
) as sql_file:
    sql_triggers = sql_file.read()


sql_forwards = sql_triggers


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0073_invoice_cancelled_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='Credit',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('label', models.CharField(max_length=300, verbose_name='Label')),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ('payer_external_id', models.CharField(max_length=250)),
                ('payer_first_name', models.CharField(max_length=250)),
                ('payer_last_name', models.CharField(max_length=250)),
                ('payer_address', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('number', models.PositiveIntegerField(default=0)),
                ('formatted_number', models.CharField(max_length=200)),
                ('assigned_amount', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ('remaining_amount', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
            ],
        ),
        migrations.AddField(
            model_name='regie',
            name='credit_number_format',
            field=models.CharField(
                default='A{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Credit number format',
            ),
        ),
        migrations.AlterField(
            model_name='counter',
            name='kind',
            field=models.CharField(
                choices=[('invoice', 'Invoice'), ('payment', 'Payment'), ('credit', 'Credit')], max_length=10
            ),
        ),
        migrations.CreateModel(
            name='CreditLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, null=True)),
                ('event_date', models.DateField()),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('quantity', models.DecimalField(decimal_places=2, max_digits=9)),
                ('unit_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('user_first_name', models.CharField(max_length=250)),
                ('user_last_name', models.CharField(max_length=250)),
                (
                    'credit',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='lines',
                        to='invoicing.credit',
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CreditAssignment',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'credit',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.credit'),
                ),
                (
                    'payment',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.payment'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='credit',
            name='regie',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
        ),
        migrations.AddConstraint(
            model_name='credit',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        ('assigned_amount__lte', django.db.models.expressions.F('total_amount')),
                        ('total_amount__gt', 0),
                    ),
                    models.Q(
                        ('assigned_amount__gte', django.db.models.expressions.F('total_amount')),
                        ('total_amount__lt', 0),
                    ),
                    models.Q(('assigned_amount', 0), ('total_amount', 0)),
                    _connector='OR',
                ),
                name='assigned_amount_check',
            ),
        ),
        migrations.RunSQL(sql=sql_forwards, reverse_sql=migrations.RunSQL.noop),
    ]
