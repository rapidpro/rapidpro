from __future__ import print_function, unicode_literals

import regex
import six

from collections import OrderedDict
from django.core.management.base import BaseCommand
from django.db.migrations import RunSQL
from django.db.migrations.executor import MigrationExecutor
from django.utils.timezone import now
from enum import Enum
from temba.sql import InstallSQL
from temba.utils import truncate


class SqlType(Enum):
    """
    The different SQL types that we can extract from migrations
    """
    INDEX = (
        r'CREATE\s+INDEX\s+(?P<name>\w+)',
        r'DROP\s+INDEX\s+(IF\s+EXISTS)?\s+(?P<name>\w+)'
    )
    FUNCTION = (
        'CREATE\s+(OR\s+REPLACE)?\s+FUNCTION\s+(?P<name>\w+)',
        'DROP\s+FUNCTION\s+(IF\s+EXISTS)?\s+(?P<name>\w+)'
    )
    TRIGGER = (
        'CREATE\s+TRIGGER\s+(?P<name>\w+)',
        'DROP\s+TRIGGER\s+(IF\s+EXISTS)?\s+(?P<name>\w+)'
    )

    def __init__(self, create_pattern, drop_pattern):
        self.create_pattern = create_pattern
        self.drop_pattern = drop_pattern

    @classmethod
    def match(cls, statement):
        """
        Checks a SQL statement to see if it's creating or dropping a known type
        """
        for sql_type in cls.__members__.values():
            for (pattern, is_create) in ((sql_type.create_pattern, True), (sql_type.drop_pattern, False)):
                m = regex.match(pattern, statement, flags=regex.IGNORECASE)
                if m:
                    return sql_type, m.groupdict()['name'], is_create
        return None


@six.python_2_unicode_compatible
class SqlObjectOperation(object):
    def __init__(self, statement, sql_type, obj_name, is_create):
        self.statement = statement
        self.sql_type = sql_type
        self.obj_name = obj_name
        self.is_create = is_create

    def __str__(self):
        return truncate(self.statement, 79).replace('\n', ' ')


class Command(BaseCommand):  # pragma: no cover
    help = "Collects SQL statements from migrations to compile a list of indexes, functions and triggers"

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir', action='store', dest='output_dir', default='temba/sql',
            help='The output directory for generated SQL files.',
        )

    def handle(self, *args, **options):
        output_dir = options.get('output_dir')
        self.verbosity = options.get('verbosity')

        self.stdout.write("Loading migrations...")

        migrations = self.load_migrations()

        self.stdout.write("Loaded %s migrations" % self.style.SUCCESS(len(migrations)))
        self.stdout.write("Extracting SQL operations...")

        operations = self.extract_operations(migrations)

        self.stdout.write("Extracted %s SQL operations" % self.style.SUCCESS(len(operations)))
        self.stdout.write("Normalizing SQL operations...")

        normalized = self.normalize_operations(operations)

        self.stdout.write("Removed %s redundant operations" % self.style.SUCCESS(len(operations) - len(normalized)))

        self.write_type_dumps(normalized, output_dir)

    def load_migrations(self):
        """
        Loads all migrations in the order they would be applied to a clean database
        """
        executor = MigrationExecutor(connection=None)

        # create the forwards plan Django would follow on an empty database
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes(), clean_start=True)

        if self.verbosity >= 2:
            for migration, _ in plan:
                self.stdout.write(" > %s" % migration)

        return [m[0] for m in plan]

    def extract_operations(self, migrations):
        """
        Extract SQL operations from the given migrations
        """
        operations = []

        for migration in migrations:
            for operation in migration.operations:
                if isinstance(operation, RunSQL) and not isinstance(operation, InstallSQL):
                    statements = operation.sql.split(';')

                    for statement in statements:
                        s = statement.strip() + ';'
                        match = SqlType.match(s)
                        if match:
                            operation = SqlObjectOperation(s, *match)
                            operations.append(operation)

                            if self.verbosity >= 2:
                                self.stdout.write(" > %s (%s)" % (operation, migration))

        return operations

    def normalize_operations(self, operations):
        """
        Removes redundant SQL operations - e.g. a CREATE X followed by a DROP X
        """
        normalized = OrderedDict()

        for operation in operations:
            # do we already have an operation for this object?
            if operation.obj_name in normalized:
                if self.verbosity >= 2:
                    self.stdout.write(" < %s" % normalized[operation.obj_name])

                del normalized[operation.obj_name]

            if operation.is_create:
                normalized[operation.obj_name] = operation
            else:
                self.stdout.write(" < %s" % operation)

        return normalized.values()

    def write_type_dumps(self, operations, output_dir):
        """
        Splits the list of SQL operations by type and dumps these to separate files
        """
        by_type = {SqlType.INDEX: [], SqlType.FUNCTION: [], SqlType.TRIGGER: []}
        for operation in operations:
            by_type[operation.sql_type].append(operation)

        if by_type[SqlType.INDEX]:
            self.write_dump('indexes', by_type[SqlType.INDEX], output_dir)
        if by_type[SqlType.FUNCTION]:
            self.write_dump('functions', by_type[SqlType.FUNCTION], output_dir)
        if by_type[SqlType.TRIGGER]:
            self.write_dump('triggers', by_type[SqlType.TRIGGER], output_dir)

    def write_dump(self, type_label, operations, output_dir):
        filename = '%s/current_%s.sql' % (output_dir, type_label)

        with open(filename, 'w') as f:
            header = '-- Generated by collect_sql on %s\n\n' % now().strftime("%Y-%m-%d %H:%M")
            f.write(header)

            for operation in operations:
                f.write(operation.statement)
                f.write('\n\n')

        self.stdout.write("Saved %s" % filename)
