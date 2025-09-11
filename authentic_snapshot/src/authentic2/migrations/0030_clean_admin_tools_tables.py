import logging

from django.db import migrations


def noop(apps, schema_editor):
    pass


def clean_admin_tools_tables(apps, schema_editor):
    try:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                'SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND'
                " table_name LIKE 'admin_tools%'"
            )
            rows = cursor.fetchall()
            for (table_name,) in rows:
                cursor.execute('DROP TABLE "%s" CASCADE' % table_name)
    except Exception:
        logging.getLogger(__name__).exception('migration authentic2.0030 failed')


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0029_auto_20201013_1614'),
    ]

    operations = [
        migrations.RunPython(clean_admin_tools_tables, noop),
    ]
