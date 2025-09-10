from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('epayment', '0005_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentbackend',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentbackend',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
