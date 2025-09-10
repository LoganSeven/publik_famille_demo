from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('basket', '0003_basket_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='validated_at',
            field=models.DateTimeField(null=True),
        ),
    ]
