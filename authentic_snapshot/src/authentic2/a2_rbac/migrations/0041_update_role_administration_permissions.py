from django.db import migrations


def update_admin_roles_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    OrganizationalUnit = apps.get_model('a2_rbac', 'OrganizationalUnit')
    Operation = apps.get_model('a2_rbac', 'Operation')
    Role = apps.get_model('a2_rbac', 'Role')
    Permission = apps.get_model('a2_rbac', 'Permission')
    User = apps.get_model('custom_user', 'User')

    view_operation, _ = Operation.objects.get_or_create(slug='view')
    search_operation, _ = Operation.objects.get_or_create(slug='search')

    target_ct = ContentType.objects.get_for_model(Role)

    def all_ous_iterator():
        yield from OrganizationalUnit.objects.all()
        yield None  # global administration not restrained to any OU

    for ou in all_ous_iterator():
        try:
            view_user_perm = Permission.objects.get(
                operation=view_operation,
                target_ct=ContentType.objects.get_for_model(ContentType),
                target_id=ContentType.objects.get_for_model(User).pk,
                ou__isnull=ou is None,
                ou=ou,
            )
        except Permission.DoesNotExist:
            # The permission does not exist, implying that role administration roles have
            # not been created in this OU yet, no migration needed.
            continue

        search_user_perm, _ = Permission.objects.get_or_create(
            operation=search_operation,
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
            ou__isnull=ou is None,
            ou=ou,
        )

        view_user_perm_roles = [role.id for role in view_user_perm.roles.all()]
        search_user_perm_roles = [role.id for role in search_user_perm.roles.all()]

        roles_qs = (
            Role.objects.prefetch_related('permissions')
            .filter(admin_scope_ct=target_ct, admin_scope_id__isnull=False, id__in=view_user_perm_roles)
            .exclude(id__in=search_user_perm_roles)
        )
        for role in roles_qs:
            role.permissions.remove(view_user_perm)
            role.permissions.add(search_user_perm)
            role.save()


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0040_role_name_idx'),
    ]

    operations = [
        migrations.RunPython(update_admin_roles_permissions, reverse_code=migrations.RunPython.noop)
    ]
