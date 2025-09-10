from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0129_payer'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='draftinvoice',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='draftjournalline',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='injectedline',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='invoice',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='payer_demat',
        ),
        migrations.RemoveField(
            model_name='journalline',
            name='payer_demat',
        ),
    ]
