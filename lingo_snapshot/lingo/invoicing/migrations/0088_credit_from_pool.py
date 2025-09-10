import django.db.models.deletion
from django.db import migrations, models
from django.utils.timezone import now


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0087_cancellation_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='date_publication',
            field=models.DateField(
                default=now().date,
                help_text='Date on which the invoice is visible on the portal.',
                verbose_name='Publication date',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='credit',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
        migrations.AddField(
            model_name='journalline',
            name='credit_line',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='journal_lines',
                to='invoicing.creditline',
            ),
        ),
    ]
