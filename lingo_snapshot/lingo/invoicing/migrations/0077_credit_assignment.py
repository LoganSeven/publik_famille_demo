import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0076_line_new_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditassignment',
            name='invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoice'
            ),
        ),
    ]
