from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0085_payment_docket'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditline',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='journalline',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250),
        ),
    ]
