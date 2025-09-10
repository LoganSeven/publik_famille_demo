from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0089_credit_cancellation'),
    ]

    operations = [
        migrations.RenameField(
            model_name='payment',
            old_name='order_id',
            new_name='transaction_id',
        ),
        migrations.RenameField(
            model_name='payment',
            old_name='order_date',
            new_name='transaction_date',
        ),
        migrations.AddField(
            model_name='payment',
            name='bank_data',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='payment',
            name='bank_transaction_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='bank_transaction_id',
            field=models.CharField(max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='order_id',
            field=models.CharField(max_length=200, null=True),
        ),
    ]
