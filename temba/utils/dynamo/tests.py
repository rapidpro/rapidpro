from temba.tests import TembaTest
from temba.utils import dynamo


class DynamoTest(TembaTest):
    def test_get_client(self):
        client1 = dynamo.get_client()
        client2 = dynamo.get_client()
        self.assertIs(client1, client2)

    def test_table_name(self):
        self.assertEqual("LocalThings", dynamo.table_name("Things"))
