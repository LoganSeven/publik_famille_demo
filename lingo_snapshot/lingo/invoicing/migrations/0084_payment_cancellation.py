import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('invoicing', '0083_credit_activity_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='cancellation_description',
            field=models.TextField(blank=True, verbose_name='Description'),
        ),
        migrations.AddField(
            model_name='payment',
            name='cancelled_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='cancelled_by',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.CreateModel(
            name='PaymentCancellationReason',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
                ('disabled', models.BooleanField(default=False, verbose_name='Disabled')),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.AddField(
            model_name='payment',
            name='cancellation_reason',
            field=models.ForeignKey(
                default='',
                verbose_name='Cancellation reason',
                on_delete=django.db.models.deletion.PROTECT,
                to='invoicing.paymentcancellationreason',
                null=True,
            ),
            preserve_default=False,
        ),
    ]
