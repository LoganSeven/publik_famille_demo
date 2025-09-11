from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_cas', '0012_copy_service_proxy_to_m2m'),
    ]

    operations = [
        migrations.DeleteModel('ServiceProxy2'),
    ]
