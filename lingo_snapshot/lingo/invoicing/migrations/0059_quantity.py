from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0058_payment_payer_external_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftjournalline',
            name='quantity',
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='quantity_type',
            field=models.CharField(
                choices=[('units', 'Units'), ('minutes', 'Minutes')], default='units', max_length=10
            ),
        ),
        migrations.AddField(
            model_name='journalline',
            name='quantity',
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name='journalline',
            name='quantity_type',
            field=models.CharField(
                choices=[('units', 'Units'), ('minutes', 'Minutes')], default='units', max_length=10
            ),
        ),
        migrations.AlterField(
            model_name='draftinvoiceline',
            name='quantity',
            field=models.DecimalField(decimal_places=2, max_digits=9),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='quantity',
            field=models.DecimalField(decimal_places=2, max_digits=9),
        ),
    ]
