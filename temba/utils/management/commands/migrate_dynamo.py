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
            name = table["TableName"]
            real_name = dynamo.table_name(name)

            # ttl isn't actually part of the create_table call
            ttlSpec = table.pop("TimeToLiveSpecification", None)

            if not self._table_exists(real_name):
                spec = table.copy()
                spec["TableName"] = real_name

                self.client.create_table(**spec)

                self.stdout.write(f"{real_name}: created")

                if ttlSpec:
                    self.client.update_time_to_live(TableName=real_name, TimeToLiveSpecification=ttlSpec)

                    self.stdout.write(f"{real_name}: updated TTL")
            else:
                self.stdout.write(f"{real_name}: already exists")

    def _table_exists(self, real_name: str) -> bool:
        try:
            self.client.describe_table(TableName=real_name)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False
