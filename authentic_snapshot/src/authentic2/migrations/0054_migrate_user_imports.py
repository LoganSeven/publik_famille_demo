from django.core.files.storage import default_storage
from django.db import migrations
from django.db.utils import IntegrityError


def create_imports(apps, schema_editor):
    from authentic2.manager import user_import

    try:
        default_storage.path('user_imports')
    except AttributeError:
        # nothing to migrate  when creating a schema with a FakeTenant
        return

    UserImport = apps.get_model('authentic2', 'UserImport')
    for uimport in user_import.UserImport.all():
        for report in uimport.reports:
            try:
                ou = report.ou
            except (UnicodeDecodeError, AttributeError):
                pass
            except Exception:
                user_import.logger.exception(
                    'User import migration failed',
                    extra={
                        'import_path': uimport.path,
                        'report_path': report.path,
                    },
                )
            else:
                if ou is not None:
                    try:
                        UserImport.objects.get_or_create(
                            uuid=uimport.uuid, ou_id=ou.id, created=uimport.created
                        )
                    except IntegrityError:
                        # ou no longer exists
                        pass
                    except Exception:
                        user_import.logger.exception(
                            'User import migration failed',
                            extra={
                                'import_path': uimport.path,
                                'report_path': report.path,
                            },
                        )
                    break


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0053_add_user_import'),
    ]

    operations = [
        migrations.RunPython(create_imports, migrations.RunPython.noop),
    ]
