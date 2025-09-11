from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0011_auto_20160209_1511'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='roleparenting',
            unique_together={('parent', 'child', 'direct')},
        ),
        migrations.AlterIndexTogether(
            name='roleparenting',
            index_together={('child', 'parent', 'direct')},
        ),
    ]
