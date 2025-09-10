from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0048_journal_lines'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='event',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='from_injected_line',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='status',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='error_status',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='event',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='from_injected_line',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='status',
        ),
    ]
