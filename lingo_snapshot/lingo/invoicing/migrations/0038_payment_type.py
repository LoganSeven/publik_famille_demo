import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0037_due_date'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentType',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, verbose_name='Identifier')),
                ('disabled', models.BooleanField(default=False, verbose_name='Disabled')),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.regie'),
                ),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='paymenttype',
            unique_together={('regie', 'slug')},
        ),
        migrations.AddField(
            model_name='payment',
            name='new_payment_type',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.paymenttype'
            ),
        ),
    ]
