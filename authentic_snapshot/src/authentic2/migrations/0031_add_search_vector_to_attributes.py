import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations


def create_trigger(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SHOW default_text_search_config')
        assert cursor.fetchone()
        cursor.execute(
            '''CREATE OR REPLACE FUNCTION authentic2_update_atv_search_vector() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.content <> OLD.content) THEN
        NEW.search_vector = to_tsvector(NEW.content);
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql'''
        )
        cursor.execute(
            '''CREATE TRIGGER authentic2_attributevalue_search_vector_trigger
BEFORE INSERT OR UPDATE OF content
ON authentic2_attributevalue
FOR EACH ROW EXECUTE PROCEDURE authentic2_update_atv_search_vector()'''
        )


def drop_trigger(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            'DROP TRIGGER IF EXISTS authentic2_attributevalue_search_vector_trigger ON'
            ' authentic2_attributevalue'
        )
        cursor.execute('DROP FUNCTION IF EXISTS authentic2_update_atv_search_vector')


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0030_clean_admin_tools_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='attributevalue',
            name='search_vector',
            field=django.contrib.postgres.search.SearchVectorField(editable=False, null=True),
        ),
        migrations.AddIndex(
            model_name='attributevalue',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['search_vector'], name='authentic2_atv_tsvector_idx'
            ),
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]
