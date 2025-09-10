from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0018_payer'),
    ]

    operations = [
        migrations.RenameField(
            model_name='draftinvoice',
            old_name='payer',
            new_name='payer_external_id',
        ),
        migrations.RenameField(
            model_name='invoice',
            old_name='payer',
            new_name='payer_external_id',
        ),
        migrations.AlterField(
            model_name='draftinvoice',
            name='payer_external_id',
            field=models.CharField(max_length=250),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='payer_external_id',
            field=models.CharField(max_length=250),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_demat',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_direct_debit',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_demat',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_direct_debit',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
