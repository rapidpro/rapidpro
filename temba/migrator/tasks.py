from celery.task import task

from .models import MigrationTask


@task(track_started=True, name="start_migration")
def start_migration(migration_task_id):
    """
    Start the migration process
    """
    migration = MigrationTask.objects.filter(id=migration_task_id).first()
    if not migration:
        return

    migration.perform()
