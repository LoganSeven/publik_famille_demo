import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0079_credit_assignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='refund_number_format',
            field=models.CharField(
                default='V{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Refund number format',
            ),
        ),
        migrations.AlterField(
            model_name='counter',
            name='kind',
            field=models.CharField(
                choices=[
                    ('invoice', 'Invoice'),
                    ('payment', 'Payment'),
                    ('credit', 'Credit'),
                    ('refund', 'Refund'),
                ],
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='creditassignment',
            name='invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoice'
            ),
        ),
        migrations.CreateModel(
            name='Refund',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('number', models.PositiveIntegerField(default=0)),
                ('formatted_number', models.CharField(max_length=200)),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=9,
                        validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                    ),
                ),
                ('payer_external_id', models.CharField(max_length=250)),
                ('payer_first_name', models.CharField(max_length=250)),
                ('payer_last_name', models.CharField(max_length=250)),
                ('payer_address', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='creditassignment',
            name='refund',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.refund'
            ),
        ),
    ]
