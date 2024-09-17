import time

from django.conf import settings
from django.core.management import BaseCommand

from temba.utils import dynamo

TABLES = [
    {
        "TableName": "ChannelLogs",
        "KeySchema": [{"AttributeName": "UUID", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "UUID", "AttributeType": "S"}],
        "TimeToLiveSpecification": {"AttributeName": "ExpiresOn", "Enabled": True},
        "BillingMode": "PAY_PER_REQUEST",
    }
]


class Command(BaseCommand):
    help = "Creates DynamoDB tables that don't already exist."

    def add_arguments(self, parser):
        parser.add_argument("--testing", action="store_true")

    def handle(self, testing: bool, *args, **kwargs):
        self.client = dynamo.get_client()

        # during tests settings.TESTING is true so table prefix is "Test" - but this command is run with
        # settings.TESTING == False, so when setting up tables for testing we need to override the prefix
        if testing:
            settings.DYNAMO_TABLE_PREFIX = "Test"

        for table in TABLES:
            self._migrate_table(table)

    def _migrate_table(self, table: dict):
        name = table["TableName"]
        real_name = dynamo.table_name(name)
        status = self._table_status(real_name)

        if status == "":
            spec = table.copy()
            spec["TableName"] = real_name

            # invoke pre-create signal to allow for table modifications
            dynamo.signals.pre_create_table.send(self.__class__, spec=spec)

            # ttl isn't actually part of the create call
            ttlSpec = spec.pop("TimeToLiveSpecification", None)

            self.stdout.write(f"Creating {real_name}...", ending="")
            self.stdout.flush()

            self._create_table(spec)

            self.stdout.write(self.style.SUCCESS(" OK"))

            if ttlSpec:
                self.client.update_time_to_live(TableName=real_name, TimeToLiveSpecification=ttlSpec)

                self.stdout.write(f"Updated TTL for {real_name}")
        else:
            self.stdout.write(f"Skipping {real_name} which already exists")

    def _create_table(self, spec: dict):
        """
        Creates the given table and waits for it to become active.
        """
        self.client.create_table(**spec)

        while True:
            time.sleep(1.0)

            if self._table_status(spec["TableName"]) == "ACTIVE":
                break

    def _table_status(self, real_name: str) -> str:
        """
        Returns the status of a table, or an empty string if it doesn't exist.
        """
        try:
            desc = self.client.describe_table(TableName=real_name)
            return desc["Table"]["TableStatus"]
        except self.client.exceptions.ResourceNotFoundException:
            return ""
