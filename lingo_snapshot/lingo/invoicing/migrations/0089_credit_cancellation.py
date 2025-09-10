import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('invoicing', '0088_credit_from_pool'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='cancellation_description',
            field=models.TextField(blank=True, verbose_name='Description'),
        ),
        migrations.AddField(
            model_name='credit',
            name='cancellation_reason',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='invoicing.invoicecancellationreason',
                verbose_name='Cancellation reason',
            ),
        ),
        migrations.AddField(
            model_name='credit',
            name='cancelled_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='credit',
            name='cancelled_by',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
            ),
        ),
    ]
