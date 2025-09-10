from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0110_line_details_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='date_invoicing',
            field=models.DateField(null=True, verbose_name='Invoicing date'),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='date_invoicing',
            field=models.DateField(null=True, verbose_name='Invoicing date'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='date_invoicing',
            field=models.DateField(null=True, verbose_name='Invoicing date'),
        ),
    ]
