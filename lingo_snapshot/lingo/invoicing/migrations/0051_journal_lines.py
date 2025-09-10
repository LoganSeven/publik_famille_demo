from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0050_journal_lines'),
    ]

    operations = [
        migrations.RenameField(
            model_name='draftinvoiceline',
            old_name='pricing_data',
            new_name='details',
        ),
        migrations.RenameField(
            model_name='invoiceline',
            old_name='pricing_data',
            new_name='details',
        ),
    ]
