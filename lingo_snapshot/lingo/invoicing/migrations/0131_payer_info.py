from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0130_payer_info'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_address',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_direct_debit',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_external_id',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_first_name',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_last_name',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_address',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_direct_debit',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_external_id',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_first_name',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_last_name',
        ),
    ]
