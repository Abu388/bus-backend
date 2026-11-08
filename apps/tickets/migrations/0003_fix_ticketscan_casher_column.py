from django.db import migrations

TABLE = 'tickets_ticketscan'
OLD_COLUMN = 'cashier_id'
NEW_COLUMN = 'casher_id'


def _column_names(connection, table):
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(cursor, table)
        }


def _rename_column(schema_editor, old_name, new_name):
    connection = schema_editor.connection
    columns = _column_names(connection, TABLE)
    if old_name in columns and new_name not in columns:
        quote = connection.ops.quote_name
        schema_editor.execute(
            f'ALTER TABLE {quote(TABLE)} '
            f'RENAME COLUMN {quote(old_name)} TO {quote(new_name)}'
        )


def forwards(apps, schema_editor):
    # Migration 0002 was edited after it had already been applied on existing
    # databases (cashier -> casher). Fresh databases already have casher_id,
    # while older databases still have cashier_id. Make both converge safely.
    _rename_column(schema_editor, OLD_COLUMN, NEW_COLUMN)


def backwards(apps, schema_editor):
    _rename_column(schema_editor, NEW_COLUMN, OLD_COLUMN)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
