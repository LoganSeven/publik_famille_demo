from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0046_journal_lines'),
    ]

    operations = [
        migrations.RenameField(
            model_name='injectedline',
            old_name='unit_amount',
            new_name='amount',
        ),
        migrations.RemoveField(
            model_name='injectedline',
            name='quantity',
        ),
    ]
