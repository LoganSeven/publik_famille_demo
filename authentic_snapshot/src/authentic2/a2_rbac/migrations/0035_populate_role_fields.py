import json

from django.db import migrations


def populate_role_fields(apps, schema_editor):
    Role = apps.get_model('a2_rbac', 'Role')

    fields = {'details', 'emails', 'emails_to_members', 'is_superuser'}
    roles = list(Role.objects.all().prefetch_related('attributes'))
    for role in roles:
        for attribute in role.attributes.all():
            if attribute.name not in fields:
                continue
            try:
                value = json.loads(attribute.value)
            except json.JSONDecodeError:
                continue

            if attribute.name == 'emails':
                if not isinstance(value, list):
                    continue
                value = [x[:254] for x in value]

            if attribute.name == 'details' and not isinstance(value, str):
                continue

            if attribute.name in ('emails_to_members', 'is_superuser') and not isinstance(value, bool):
                continue

            setattr(role, attribute.name, value)

    Role.objects.bulk_update(roles, fields, batch_size=1000)


def reverse_populate_role_fields(apps, schema_editor):
    Role = apps.get_model('a2_rbac', 'Role')
    RoleAttribute = apps.get_model('a2_rbac', 'RoleAttribute')

    fields = ['details', 'emails', 'emails_to_members']
    attributes = []
    for role in Role.objects.all():
        for field in fields:
            attributes.append(
                RoleAttribute(
                    role_id=role.pk, name=field, kind='json', value=json.dumps(getattr(role, field))
                )
            )

    RoleAttribute.objects.bulk_create(attributes, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0034_new_role_fields'),
    ]

    operations = [
        migrations.RunPython(populate_role_fields, reverse_code=reverse_populate_role_fields),
    ]
