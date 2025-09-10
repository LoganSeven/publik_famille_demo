from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0131_payer_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='credit',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='journalline',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='journalline',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='refund',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='refund',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
    ]
