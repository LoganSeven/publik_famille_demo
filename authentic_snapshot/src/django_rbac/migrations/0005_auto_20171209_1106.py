from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('django_rbac', '0004_auto_20150708_1337'),
    ]

    operations = [
        migrations.AlterField(
            model_name='operation',
            name='name',
            field=models.CharField(max_length=128, verbose_name='name'),
        ),
    ]
