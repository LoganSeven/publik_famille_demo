from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0052_journal_lines'),
    ]

    operations = [
        migrations.AddField(
            model_name='counter',
            name='kind',
            field=models.CharField(
                choices=[('invoice', 'Invoice'), ('payment', 'Payment')], default='invoice', max_length=10
            ),
        ),
        migrations.AlterField(
            model_name='counter',
            name='kind',
            field=models.CharField(choices=[('invoice', 'Invoice'), ('payment', 'Payment')], max_length=10),
        ),
        migrations.AlterUniqueTogether(
            name='counter',
            unique_together={('regie', 'name', 'kind')},
        ),
        migrations.RenameField(
            model_name='regie',
            old_name='number_format',
            new_name='invoice_number_format',
        ),
        migrations.AlterField(
            model_name='regie',
            name='invoice_number_format',
            field=models.CharField(
                default='F{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Invoice number format',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='formatted_number',
            field=models.CharField(default='0', max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payment',
            name='number',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='regie',
            name='payment_number_format',
            field=models.CharField(
                default='R{regie_id:02d}-{yy}-{mm}-{number:07d}',
                max_length=100,
                verbose_name='Payment number format',
            ),
        ),
    ]
