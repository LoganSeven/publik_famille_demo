import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0084_payment_cancellation'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='docket_number_format',
            field=models.CharField(
                default='B{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Payment docket number format',
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
                    ('docket', 'Payment Docket'),
                ],
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='PaymentDocket',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('number', models.PositiveIntegerField(default=0)),
                ('formatted_number', models.CharField(max_length=200)),
                ('date_end', models.DateField(verbose_name='End date')),
                ('draft', models.BooleanField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('payment_types', models.ManyToManyField(to='invoicing.PaymentType')),
                ('payment_types_info', models.JSONField(blank=True, default=dict)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='payment',
            name='docket',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.paymentdocket'
            ),
        ),
    ]
