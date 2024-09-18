from temba.tests.base import TembaTest

from . import find_uuid, is_uuid, uuid7


class UUIDTest(TembaTest):
    def test_is_uuid(self):
        self.assertFalse(is_uuid(None))
        self.assertFalse(is_uuid(""))
        self.assertFalse(is_uuid("1234567890-xx"))
        self.assertTrue(is_uuid("d749e4e9-2898-4e47-9418-7a89d9e51359"))
        self.assertFalse(is_uuid("http://d749e4e9-2898-4e47-9418-7a89d9e51359/"))

    def test_find_uuid(self):
        self.assertEqual(None, find_uuid(""))
        self.assertEqual(None, find_uuid("xx"))
        self.assertEqual("d749e4e9-2898-4e47-9418-7a89d9e51359", find_uuid("d749e4e9-2898-4e47-9418-7a89d9e51359"))
        self.assertEqual(
            "d749e4e9-2898-4e47-9418-7a89d9e51359", find_uuid("http://d749e4e9-2898-4e47-9418-7a89d9e51359/")
        )

    def test_uuid7(self):
        u1 = uuid7()
        u2 = uuid7()
        self.assertEqual(36, len(u1))
        self.assertEqual(36, len(u2))
        self.assertNotEqual(u1, u2)
