import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0120_payment_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='CreditCancellationReason',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
                ('disabled', models.BooleanField(default=False, verbose_name='Disabled')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True, null=True)),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.RenameField(
            model_name='credit',
            old_name='cancellation_reason',
            new_name='old_cancellation_reason',
        ),
        migrations.AddField(
            model_name='credit',
            name='new_cancellation_reason',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='invoicing.creditcancellationreason',
                verbose_name='Cancellation reason',
            ),
        ),
    ]
