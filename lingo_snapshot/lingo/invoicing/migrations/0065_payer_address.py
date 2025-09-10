from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0064_invoice_line_payment'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='injectedline',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='journalline',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_address',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
    ]
