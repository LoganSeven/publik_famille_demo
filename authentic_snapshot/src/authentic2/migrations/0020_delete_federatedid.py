from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0019_auto_20170309_1529'),
    ]

    operations = [
        migrations.DeleteModel(
            name='FederatedId',
        ),
    ]
