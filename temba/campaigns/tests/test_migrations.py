from temba.campaigns.models import Campaign
from temba.tests import MigrationTest


class ArchiveWithDeletedGroupsTest(MigrationTest):
    app = "campaigns"
    migrate_from = "0059_squashed"
    migrate_to = "0060_archive_deleted_groups"

    def setUpBeforeMigration(self, apps):
        group1 = self.create_group("Group 1", contacts=[])
        group2 = self.create_group("Group 2", contacts=[])
        group2.release(self.admin)

        self.campaign1 = Campaign.create(self.org, self.admin, "Campaign 1", group1)
        self.campaign2 = Campaign.create(self.org, self.admin, "Campaign 2", group2)

    def test_migration(self):
        self.campaign1.refresh_from_db()
        self.campaign2.refresh_from_db()

        self.assertFalse(self.campaign1.is_archived)
        self.assertTrue(self.campaign2.is_archived)
