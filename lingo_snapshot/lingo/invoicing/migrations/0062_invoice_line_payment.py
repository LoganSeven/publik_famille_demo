import os
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

with open(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        '..',
        'sql',
        'invoice_triggers_for_amount.sql',
    )
) as sql_file:
    sql_triggers = sql_file.read()


sql_forwards = sql_triggers


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0061_pdf_appearance'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceLinePayment',
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
                    'line',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoiceline'
                    ),
                ),
                (
                    'payment',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.payment'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='paid_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='remaining_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.AddConstraint(
            model_name='invoiceline',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        ('paid_amount__lte', django.db.models.expressions.F('total_amount')),
                        ('total_amount__gt', 0),
                    ),
                    models.Q(
                        ('paid_amount__gte', django.db.models.expressions.F('total_amount')),
                        ('total_amount__lt', 0),
                    ),
                    models.Q(('paid_amount', 0), ('total_amount', 0)),
                    _connector='OR',
                ),
                name='paid_amount_check',
            ),
        ),
        migrations.RunSQL(sql=sql_forwards, reverse_sql=migrations.RunSQL.noop),
    ]
