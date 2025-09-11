from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0014_attributevalue_verified'),
    ]

    operations = [
        migrations.AlterField(
            model_name='passwordreset',
            name='user',
            field=models.OneToOneField(
                verbose_name='user', to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE
            ),
        ),
    ]
