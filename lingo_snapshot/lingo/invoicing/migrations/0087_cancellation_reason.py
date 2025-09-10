import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('invoicing', '0086_accounting_code'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceCancellationReason',
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
            model_name='invoice',
            name='cancellation_description',
            field=models.TextField(blank=True, verbose_name='Description'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='cancelled_by',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='cancellation_reason',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='invoicing.invoicecancellationreason',
                verbose_name='Cancellation reason',
            ),
        ),
    ]
