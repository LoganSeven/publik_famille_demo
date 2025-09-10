import django.core.serializers.json
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0107_previous_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditline',
            name='details',
            field=models.JSONField(default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
        ),
        migrations.AddField(
            model_name='creditline',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
    ]
