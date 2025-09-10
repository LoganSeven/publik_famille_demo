from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0036_payer'),
    ]

    operations = [
        migrations.RenameField(
            model_name='campaign',
            old_name='date_issue',
            new_name='date_due',
        ),
        migrations.AlterField(
            model_name='campaign',
            name='date_due',
            field=models.DateField(
                help_text='Date on which invoices are no longer payable at the counter.',
                verbose_name='Due date',
            ),
        ),
        migrations.RenameField(
            model_name='draftinvoice',
            old_name='date_issue',
            new_name='date_due',
        ),
        migrations.AlterField(
            model_name='draftinvoice',
            name='date_due',
            field=models.DateField(
                help_text='Date on which the invoice is no longer payable at the counter.',
                verbose_name='Due date',
            ),
        ),
        migrations.RenameField(
            model_name='invoice',
            old_name='date_issue',
            new_name='date_due',
        ),
        migrations.AlterField(
            model_name='invoice',
            name='date_due',
            field=models.DateField(
                help_text='Date on which the invoice is no longer payable at the counter.',
                verbose_name='Due date',
            ),
        ),
    ]
