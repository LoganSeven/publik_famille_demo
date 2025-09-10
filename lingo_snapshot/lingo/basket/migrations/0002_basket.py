import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('invoicing', '0072_payment_info'),
        ('basket', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Basket',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('payer_nameid', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
                ('payer_first_name', models.CharField(max_length=250)),
                ('payer_last_name', models.CharField(max_length=250)),
                ('payer_address', models.TextField()),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('open', 'open'),
                            ('tobepaid', 'to be paid'),
                            ('completed', 'completed'),
                            ('cancelled', 'cancelled'),
                            ('expired', 'expired'),
                        ],
                        default='open',
                        max_length=10,
                    ),
                ),
                ('paid_at', models.DateTimeField(null=True)),
                ('completed_at', models.DateTimeField(null=True)),
                ('cancelled_at', models.DateTimeField(null=True)),
                ('expired_at', models.DateTimeField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'draft_invoice',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to='invoicing.draftinvoice'
                    ),
                ),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
        ),
        migrations.CreateModel(
            name='BasketLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('user_external_id', models.CharField(max_length=250)),
                ('user_first_name', models.CharField(max_length=250)),
                ('user_last_name', models.CharField(max_length=250)),
                ('information_message', models.TextField(blank=True)),
                ('group_items', models.BooleanField(default=False)),
                ('closed', models.BooleanField(default=False)),
                ('form_url', models.URLField(blank=True)),
                ('validation_callback_url', models.URLField(blank=True)),
                ('payment_callback_url', models.URLField(blank=True)),
                ('credit_callback_url', models.URLField(blank=True)),
                ('cancel_callback_url', models.URLField(blank=True)),
                ('expiration_callback_url', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'basket',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='basket.basket'),
                ),
            ],
            options={
                'unique_together': {('basket', 'user_external_id')},
            },
        ),
        migrations.CreateModel(
            name='BasketLineItem',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('label', models.CharField(max_length=200)),
                ('subject', models.CharField(blank=True, max_length=200)),
                ('details', models.TextField(blank=True)),
                ('quantity', models.DecimalField(decimal_places=2, max_digits=9)),
                ('unit_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'line',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='items',
                        to='basket.basketline',
                    ),
                ),
            ],
        ),
    ]
