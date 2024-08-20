from django.conf import settings
from django.core.management import BaseCommand

from temba.utils import dynamo

TABLES = [
    {
        "TableName": "ChannelLogsAttached",
        "KeySchema": [{"AttributeName": "UUID", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "UUID", "AttributeType": "S"}],
        "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        "TimeToLiveSpecification": {"AttributeName": "ExpireOn", "Enabled": True},
    }
]


class Command(BaseCommand):
    help = "Creates DynamoDB tables that don't already exist."

    def handle(self, *args, **kwargs):
        client = dynamo.get_client()

        for table in TABLES:
            # add optional prefix to name to allow multiple deploys in same region
            name = settings.DYNAMO_TABLE_PREFIX + table["TableName"]
            table["TableName"] = name

            # ttl isn't actually part of the create_table call
            ttlSpec = table.pop("TimeToLiveSpecification", None)

            try:
                client.describe_table(TableName=table["TableName"])

                self.stdout.write(f"{table['TableName']}: already exists")
            except client.exceptions.ResourceNotFoundException:
                client.create_table(**table)

                if ttlSpec:
                    client.update_time_to_live(
                        TableName=table["TableName"],
                        TimeToLiveSpecification=ttlSpec,
                    )

                self.stdout.write(f"{table['TableName']}: created")
