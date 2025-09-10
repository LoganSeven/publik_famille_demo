from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0006_accounting_code_max_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingdate',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='billingdate',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='criteria',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='criteria',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='pricingcriteriacategory',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='pricingcriteriacategory',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
