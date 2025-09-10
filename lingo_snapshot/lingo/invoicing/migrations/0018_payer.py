from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0017_campaign_regie'),
    ]

    operations = [
        migrations.RenameField(
            model_name='draftinvoiceline',
            old_name='user_name',
            new_name='user_last_name',
        ),
        migrations.RenameField(
            model_name='invoiceline',
            old_name='user_name',
            new_name='user_last_name',
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='payer_demat',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='payer_direct_debit',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='user_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='injectedline',
            name='payer_demat',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='injectedline',
            name='payer_direct_debit',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='injectedline',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='injectedline',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='payer_demat',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='payer_direct_debit',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='user_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
