from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0024_campaign_finalized'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='paid_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.AddField(
            model_name='invoice',
            name='remaining_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=9,
                        validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                    ),
                ),
                (
                    'payment_type',
                    models.CharField(choices=[], max_length=20),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
        ),
        migrations.CreateModel(
            name='InvoicePayment',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=9,
                        validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'invoice',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoice'),
                ),
                (
                    'payment',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.payment'),
                ),
            ],
        ),
    ]
