from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0051_journal_lines'),
    ]

    operations = [
        migrations.AlterField(
            model_name='draftinvoiceline',
            name='quantity',
            field=models.IntegerField(),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='quantity',
            field=models.IntegerField(),
        ),
    ]
