from __future__ import unicode_literals

import time

from django.core.management.base import BaseCommand, CommandError
from django.core.management.sql import emit_pre_migrate_signal, emit_post_migrate_signal
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import AmbiguityError
from importlib import import_module

APPLY_FUNCTION = 'apply_manual'


class Command(BaseCommand):  # pragma: no cover
    help = "Applies a migration manually which may have been previously applied or faked"

    def add_arguments(self, parser):
        parser.add_argument('app_label',
                            help='App label of an application to synchronize the state.')
        parser.add_argument('migration_name',
                            help='Database state will be brought to the state after that migration.')
        parser.add_argument('--record', action='store_true', dest='record', default=False,
                            help='Record migration as applied.')

    def handle(self, app_label, migration_name, *args, **options):
        self.verbosity = options.get('verbosity')
        interactive = options.get('interactive')
        record = options.get('record')

        connection = connections[DEFAULT_DB_ALIAS]
        connection.prepare_database()
        executor = MigrationExecutor(connection, None)

        # before anything else, see if there's conflicting apps and drop out hard if there are any
        conflicts = executor.loader.detect_conflicts()
        if conflicts:
            name_str = "; ".join("%s in %s" % (", ".join(names), app) for app, names in conflicts.items())
            raise CommandError(
                "Conflicting migrations detected (%s).\nTo fix them run "
                "'python manage.py makemigrations --merge'" % name_str
            )

        if app_label not in executor.loader.migrated_apps:
            raise CommandError(
                "App '%s' does not have migrations (you cannot selectively sync unmigrated apps)" % app_label
            )
        try:
            migration = executor.loader.get_migration_by_prefix(app_label, migration_name)
        except AmbiguityError:
            raise CommandError(
                "More than one migration matches '%s' in app '%s'. Please be more specific." %
                (migration_name, app_label)
            )
        except KeyError:
            raise CommandError("Cannot find a migration matching '%s' from app '%s'." % (migration_name, app_label))

        migration_module = import_module(migration.__module__)

        # check migration can be run offline
        apply_function = getattr(migration_module, APPLY_FUNCTION, None)
        if not apply_function or not callable(apply_function):
            raise CommandError("Migration %s does not contain function named '%s'" % (migration, APPLY_FUNCTION))

        plan = executor.migration_plan([(app_label, migration.name)])
        if record and not plan:
            raise CommandError("Migration %s has already been applied" % migration)

        emit_pre_migrate_signal(self.verbosity, interactive, connection.alias)

        self.stdout.write(self.style.MIGRATE_HEADING("Operations to perform:"))
        self.stdout.write("  Manually apply migration %s" % migration)
        if record:
            self.stdout.write("  Record migration %s as applied" % migration)
        self.stdout.write(self.style.MIGRATE_HEADING("Manual migration:"))

        self.apply_migration(migration, apply_function)

        if record:
            self.record_migration(migration, executor)

        # send the post_migrate signal, so individual apps can do whatever they need to do at this point.
        emit_post_migrate_signal(self.verbosity, interactive, connection.alias)

    def apply_migration(self, migration, apply_function):
        compute_time = self.verbosity > 1

        self.stdout.write("  Applying %s... " % migration, ending="")
        self.stdout.flush()

        start = time.time()

        apply_function()

        elapsed = " (%.3fs)" % (time.time() - start) if compute_time else ""

        self.stdout.write(self.style.SUCCESS("OK" + elapsed))

    def record_migration(self, migration, executor):
        self.stdout.write("  Recording %s... " % migration, ending="")
        self.stdout.flush()

        executor.recorder.record_applied(migration.app_label, migration.name)

        self.stdout.write(self.style.SUCCESS("DONE"))
