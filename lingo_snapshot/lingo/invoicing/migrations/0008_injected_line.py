import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0007_pool_exception'),
    ]

    operations = [
        migrations.CreateModel(
            name='InjectedLine',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('event_date', models.DateField()),
                ('slug', models.SlugField(max_length=250)),
                ('label', models.CharField(max_length=260)),
                ('quantity', models.FloatField()),
                ('unit_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=9)),
                ('user_external_id', models.CharField(max_length=250)),
                ('payer_external_id', models.CharField(max_length=250)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.Regie'),
                ),
            ],
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='from_injected_line',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.InjectedLine'
            ),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='from_injected_line',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.InjectedLine'
            ),
        ),
    ]
