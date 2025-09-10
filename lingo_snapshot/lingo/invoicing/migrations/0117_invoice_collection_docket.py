import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0116_date_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='collection_number_format',
            field=models.CharField(
                default='T{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Invoice collection docket number format',
            ),
        ),
        migrations.AlterField(
            model_name='counter',
            name='kind',
            field=models.CharField(
                choices=[
                    ('invoice', 'Invoice'),
                    ('collection', 'Collection docket'),
                    ('payment', 'Payment'),
                    ('credit', 'Credit'),
                    ('refund', 'Refund'),
                    ('docket', 'Payment Docket'),
                ],
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='CollectionDocket',
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
                ('minimum_threshold', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='invoice',
            name='collection',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.collectiondocket'
            ),
        ),
    ]
