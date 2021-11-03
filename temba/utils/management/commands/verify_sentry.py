from django.core.management import BaseCommand

from celery import shared_task


@shared_task
def failing_task():
    foo = 1 / 0
    print(foo)


class Command(BaseCommand):
    help = "Verify Sentry reports with verify_sentry"

    def handle(self, *args, **kwargs):

        failing_task.delay()
        foo = 1 / 0
        print(foo)
