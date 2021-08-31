from temba.contacts.models import ContactImport, ExportContactsTask
from temba.orgs.models import OrgRole
from temba.tests import TembaTest

from .models import Log


class NotificationTest(TembaTest):
    def assert_notifications(self, log_type: str, log_obj_field: str, log_obj, users: set):
        # check last log
        log = Log.objects.order_by("id").last()
        self.assertEqual(log_type, log.log_type)
        self.assertEqual(log_obj, getattr(log, log_obj_field))

        # check who was notified
        self.assertEqual(users, {n.user for n in log.notifications.all()})

    def test_import_started(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=0, created_by=self.editor, modified_by=self.editor
        )

        Log.import_started(imp)

        # we don't notify the user that started the import but we do notify other admins
        self.assert_notifications("import:started", "contact_import", imp, {self.admin})

    def test_exports(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        export = ExportContactsTask.create(self.org, self.editor)

        Log.export_started(export)

        # we don't notify the user that started the export but we do notify other admins
        self.assert_notifications("export:started", "contact_export", export, {self.admin})

        Log.export_completed(export)

        # we only notify the user that started the export
        self.assert_notifications("export:completed", "contact_export", export, {self.editor})
