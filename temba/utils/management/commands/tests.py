from io import StringIO

from django.core.management import call_command
from django.test.utils import override_settings

from temba.tests import TembaTest
from temba.utils import dynamo


class MigrateDynamoTest(TembaTest):
    def tearDown(self):
        client = dynamo.get_client()

        for table_name in client.list_tables()["TableNames"]:
            if table_name.startswith("Temp"):
                client.delete_table(TableName=table_name)

        return super().tearDown()

    @override_settings(DYNAMO_TABLE_PREFIX="Temp")
    def test_migrate_dynamo(self):
        def pre_create_table(sender, spec, **kwargs):
            spec["Tags"] = [{"Key": "Foo", "Value": "Bar"}]

        dynamo.signals.pre_create_table.connect(pre_create_table)

        out = StringIO()
        call_command("migrate_dynamo", stdout=out)

        self.assertIn("Creating TempChannelLogs", out.getvalue())

        client = dynamo.get_client()
        desc = client.describe_table(TableName="TempChannelLogs")
        self.assertEqual("ACTIVE", desc["Table"]["TableStatus"])

        out = StringIO()
        call_command("migrate_dynamo", stdout=out)

        self.assertIn("Skipping TempChannelLogs", out.getvalue())
