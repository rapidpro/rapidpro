import math
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from temba.msgs.models import Msg
from temba.utils import chunk_list

BATCH_SIZE = 1000
DEFAULT_BATCH = 1000
DEFAULT_TPS = 1


class Command(BaseCommand):  # pragma: no cover
    help = "Moves msgs out of a WIRED state and into an ERRORED state with a scheduled next_attempt"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="file_path",
            required=True,
            help="Path to the file of msg ids",
        )
        parser.add_argument(
            "--batch",
            type=int,
            action="store",
            dest="batch_size",
            default=DEFAULT_BATCH,
            help="Size of batches of messages to update and schedule",
        )
        parser.add_argument(
            "--tps",
            type=int,
            action="store",
            dest="tps",
            default=DEFAULT_TPS,
            help="The desired TPS",
        )

    def handle(self, file_path: str, batch_size: int, tps: int, *args, **options):
        with open(file_path) as id_file:
            msg_ids = [int(line) for line in id_file.readlines() if line]
            msg_ids = sorted(msg_ids)

        self.stdout.write(f"> loaded {len(msg_ids)} msg ids from {file_path}")

        num_batches = math.ceil(len(msg_ids) / batch_size)
        batch_send_time = int(batch_size / tps)  # estimated time to send a batch in seconds
        batch_num = 0
        next_attempt = timezone.now()

        self.stdout.write(f"> estimated batch send time of {batch_send_time} seconds at {tps} TPS")

        for id_batch in chunk_list(msg_ids, batch_size):
            # only fetch messages which are WIRED and have never errored
            batch = Msg.objects.filter(id__in=id_batch, status=Msg.STATUS_WIRED, error_count=0)
            num_updated = batch.update(status=Msg.STATUS_ERRORED, error_count=1, next_attempt=next_attempt)

            self.stdout.write(
                f"> batch {batch_num+1}/{num_batches}"
                f" - dewired {num_updated} msg ids, next_attempt={next_attempt.isoformat()}"
            )

            batch_num += 1
            next_attempt = next_attempt + timedelta(seconds=batch_send_time)
