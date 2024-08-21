from django.conf import settings
from django.core.management import BaseCommand

from temba.utils import dynamo

TABLES = [
    {
        "TableName": "ChannelLogsAttached",
        "KeySchema": [{"AttributeName": "UUID", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "UUID", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
        "TimeToLiveSpecification": {"AttributeName": "ExpireOn", "Enabled": True},
    }
]


class Command(BaseCommand):
    help = "Creates DynamoDB tables that don't already exist."

    def handle(self, *args, **kwargs):
        self.client = dynamo.get_client()

        for table in TABLES:
            # add optional prefix to name to allow multiple deploys in same region
            name = settings.DYNAMO_TABLE_PREFIX + table["TableName"]
            table["TableName"] = name

            # ttl isn't actually part of the create_table call
            ttlSpec = table.pop("TimeToLiveSpecification", None)

            if not self._table_exists(name):
                self.client.create_table(**table)

                self.stdout.write(f"{name}: created")

                if ttlSpec:
                    self.client.update_time_to_live(TableName=name, TimeToLiveSpecification=ttlSpec)

                    self.stdout.write(f"{name}: updated TTL")
            else:
                self.stdout.write(f"{name}: already exists")

    def _table_exists(self, name: str) -> bool:
        try:
            self.client.describe_table(TableName=name)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False
