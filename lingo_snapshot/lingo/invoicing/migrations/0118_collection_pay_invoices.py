from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0117_invoice_collection_docket'),
    ]

    operations = [
        migrations.AddField(
            model_name='collectiondocket',
            name='pay_invoices',
            field=models.BooleanField(
                default=False,
                help_text='When the collection is validated, add a "Collect" type payment to the collected invoices.',
                verbose_name='Pay invoices',
            ),
        ),
    ]
