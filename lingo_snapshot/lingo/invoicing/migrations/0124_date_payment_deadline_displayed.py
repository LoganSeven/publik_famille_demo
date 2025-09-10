from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0123_credit_cancellation_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='date_payment_deadline_displayed',
            field=models.DateField(
                blank=True,
                help_text='Payment deadline displayed to user on the portal. Leave empty to display the effective payment deadline.',
                null=True,
                verbose_name='Displayed payment deadline',
            ),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='date_payment_deadline_displayed',
            field=models.DateField(
                help_text='Payment deadline displayed to user on the portal. Leave empty to display the effective payment deadline.',
                null=True,
                verbose_name='Displayed payment deadline',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='date_payment_deadline_displayed',
            field=models.DateField(
                help_text='Payment deadline displayed to user on the portal. Leave empty to display the effective payment deadline.',
                null=True,
                verbose_name='Displayed payment deadline',
            ),
        ),
        migrations.AlterField(
            model_name='campaign',
            name='date_payment_deadline',
            field=models.DateField(
                help_text='Date on which invoices are no longer payable online.',
                verbose_name='Effective payment deadline',
            ),
        ),
        migrations.AlterField(
            model_name='draftinvoice',
            name='date_payment_deadline',
            field=models.DateField(
                help_text='Date on which the invoice is no longer payable online.',
                verbose_name='Effective payment deadline',
            ),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='date_payment_deadline',
            field=models.DateField(
                help_text='Date on which the invoice is no longer payable online.',
                verbose_name='Effective payment deadline',
            ),
        ),
        migrations.AlterField(
            model_name='paymentdocket',
            name='date_end',
            field=models.DateField(verbose_name='Stop date'),
        ),
    ]
