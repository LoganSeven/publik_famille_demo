from django.db import migrations

from authentic2.migrations import CreatePartialIndexes


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0019_organizationalunit_show_username'),
    ]

    operations = [
        CreatePartialIndexes(
            'Role',
            'a2_rbac_role',
            'a2_rbac_role_name_unique_idx',
            ('ou_id',),
            ('name',),
            null_columns=('admin_scope_ct_id',),
        ),
    ]
