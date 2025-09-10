import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0022_campaign_invalid'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='date_debit',
            field=models.DateField(default=django.utils.timezone.now, verbose_name='Debit date'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='campaign',
            name='date_payment_deadline',
            field=models.DateField(default=django.utils.timezone.now, verbose_name='Payment deadline'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='campaign',
            name='date_publication',
            field=models.DateField(
                help_text='Date on which invoices are visible on the portal.', verbose_name='Publication date'
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='date_debit',
            field=models.DateField(null=True, verbose_name='Debit date'),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='date_payment_deadline',
            field=models.DateField(default=django.utils.timezone.now, verbose_name='Payment deadline'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='date_publication',
            field=models.DateField(
                help_text='Date on which the invoice is visible on the portal.',
                verbose_name='Publication date',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='date_debit',
            field=models.DateField(null=True, verbose_name='Debit date'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='date_payment_deadline',
            field=models.DateField(default=django.utils.timezone.now, verbose_name='Payment deadline'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='date_publication',
            field=models.DateField(
                help_text='Date on which the invoice is visible on the portal.',
                verbose_name='Publication date',
            ),
            preserve_default=False,
        ),
    ]
