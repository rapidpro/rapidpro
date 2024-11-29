from temba.contacts.models import ContactGroup
from temba.tests import MigrationTest


class FixStatusGroupNamesTest(MigrationTest):
    app = "contacts"
    migrate_from = "0192_alter_contactnote_text"
    migrate_to = "0193_fix_status_group_names"

    def setUpBeforeMigration(self, apps):
        # make org 1 look like an org with the old system groups
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_ACTIVE).update(name="Active")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_BLOCKED).update(name="Blocked")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_STOPPED).update(name="Stopped")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_ARCHIVED).update(name="Archived")

        self.group1 = self.create_group("Active Contacts", contacts=[])

    def test_migration(self):
        self.assertEqual("\\Active", self.org.groups.get(group_type=ContactGroup.TYPE_DB_ACTIVE).name)
        self.assertEqual("\\Blocked", self.org.groups.get(group_type=ContactGroup.TYPE_DB_BLOCKED).name)
        self.assertEqual("\\Stopped", self.org.groups.get(group_type=ContactGroup.TYPE_DB_STOPPED).name)
        self.assertEqual("\\Archived", self.org.groups.get(group_type=ContactGroup.TYPE_DB_ARCHIVED).name)

        self.assertEqual("\\Active", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_ACTIVE).name)
        self.assertEqual("\\Blocked", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_BLOCKED).name)
        self.assertEqual("\\Stopped", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_STOPPED).name)
        self.assertEqual("\\Archived", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_ARCHIVED).name)

        # check user group unaffected
        self.group1.refresh_from_db()
        self.assertEqual("Active Contacts", self.group1.name)
