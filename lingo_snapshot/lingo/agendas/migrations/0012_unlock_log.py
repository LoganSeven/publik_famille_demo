import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0118_collection_pay_invoices'),
        ('agendas', '0011_date_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgendaUnlockLog',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('date_unlock', models.DateTimeField(auto_now_add=True)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'agenda',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agendas.agenda'),
                ),
                (
                    'campaign',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='invoicing.campaign'),
                ),
            ],
        ),
    ]
