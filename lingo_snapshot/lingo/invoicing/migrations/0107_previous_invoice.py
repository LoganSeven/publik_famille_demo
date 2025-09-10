import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0106_line_form_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='previous_invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to='invoicing.invoice'
            ),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='previous_invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to='invoicing.invoice'
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='previous_invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.SET_NULL, to='invoicing.invoice'
            ),
        ),
    ]
