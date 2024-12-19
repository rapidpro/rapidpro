from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch

from openpyxl import load_workbook

from django.core.files.storage import default_storage

from temba.archives.models import Archive
from temba.msgs.models import Attachment, MessageExport, Msg, SystemLabel
from temba.orgs.models import Export
from temba.tests import TembaTest


class MessageExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", urns=["tel:789", "tel:123"])
        self.frank = self.create_contact("Frank Blow", phone="321")
        self.kevin = self.create_contact("Kevin Durant", phone="987")

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def _export(self, system_label, label, start_date, end_date, with_groups=(), with_fields=()):
        export = MessageExport.create(
            self.org,
            self.admin,
            start_date,
            end_date,
            system_label,
            label,
            with_groups=with_groups,
            with_fields=with_fields,
        )
        with self.mockReadOnly():
            export.perform()

        return load_workbook(filename=default_storage.open(f"orgs/{self.org.id}/message_exports/{export.uuid}.xlsx"))

    def test_export_from_archives(self):
        self.joe.name = "Jo\02e Blow"
        self.joe.save(update_fields=("name",))

        self.org.created_on = datetime(2017, 1, 1, 9, tzinfo=tzone.utc)
        self.org.save()

        flow = self.create_flow("Color Flow")

        msg1 = self.create_incoming_msg(self.joe, "hello 1", created_on=datetime(2017, 1, 1, 10, tzinfo=tzone.utc))
        msg2 = self.create_incoming_msg(
            self.frank, "hello 2", created_on=datetime(2017, 1, 2, 10, tzinfo=tzone.utc), flow=flow
        )
        msg3 = self.create_incoming_msg(self.joe, "hello 3", created_on=datetime(2017, 1, 3, 10, tzinfo=tzone.utc))

        # outbound message that has no channel or URN
        msg4 = self.create_outgoing_msg(
            self.joe,
            "hello 4",
            failed_reason=Msg.FAILED_NO_DESTINATION,
            created_on=datetime(2017, 1, 4, 10, tzinfo=tzone.utc),
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_incoming_msg(
            self.joe,
            "Media message",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=tzone.utc),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_outgoing_msg(
            self.joe, "Hey out 6", status=Msg.STATUS_SENT, created_on=datetime(2017, 1, 6, 10, tzinfo=tzone.utc)
        )
        msg7 = self.create_outgoing_msg(
            self.joe, "Hey out 7", status=Msg.STATUS_DELIVERED, created_on=datetime(2017, 1, 7, 10, tzinfo=tzone.utc)
        )
        msg8 = self.create_outgoing_msg(
            self.joe, "Hey out 8", status=Msg.STATUS_ERRORED, created_on=datetime(2017, 1, 8, 10, tzinfo=tzone.utc)
        )
        msg9 = self.create_outgoing_msg(
            self.joe, "Hey out 9", status=Msg.STATUS_FAILED, created_on=datetime(2017, 1, 9, 10, tzinfo=tzone.utc)
        )

        self.assertEqual(msg5.get_attachments(), [Attachment("audio", "http://rapidpro.io/audio/sound.mp3")])

        # label first message
        label = self.create_label("la\02bel1")
        label.toggle_label([msg1], add=True)

        # archive last message
        msg3.visibility = Msg.VISIBILITY_ARCHIVED
        msg3.save()

        # archive 6 msgs
        self.create_archive(
            Archive.TYPE_MSG,
            "D",
            msg5.created_on.date(),
            [m.as_archive_json() for m in (msg1, msg2, msg3, msg4, msg5, msg6)],
        )

        with patch("django.core.files.storage.default_storage.delete"):
            msg2.delete()
            msg3.delete()
            msg4.delete()
            msg5.delete()
            msg6.delete()

        # create an archive earlier than our org creation date so we check that it isn't included
        self.create_archive(Archive.TYPE_MSG, "D", self.org.created_on - timedelta(days=2), [msg7.as_archive_json()])

        msg7.delete()

        # export all visible messages (i.e. not msg3) using export_all param
        with self.assertNumQueries(18):
            workbook = self._export(None, None, date(2000, 9, 1), date(2022, 9, 1))

        expected_headers = [
            "Date",
            "Contact UUID",
            "Contact Name",
            "URN Scheme",
            "URN Value",
            "Flow",
            "Direction",
            "Text",
            "Attachments",
            "Status",
            "Channel",
            "Labels",
        ]

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
                [
                    msg2.created_on,
                    msg2.contact.uuid,
                    "Frank Blow",
                    "tel",
                    "321",
                    "Color Flow",
                    "IN",
                    "hello 2",
                    "",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [msg4.created_on, msg1.contact.uuid, "Joe Blow", "", "", "", "OUT", "hello 4", "", "failed", "", ""],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
                [
                    msg8.created_on,
                    msg8.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "OUT",
                    "Hey out 8",
                    "",
                    "errored",
                    "Test Channel",
                    "",
                ],
                [
                    msg9.created_on,
                    msg9.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "OUT",
                    "Hey out 9",
                    "",
                    "failed",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        workbook = self._export(SystemLabel.TYPE_INBOX, None, msg5.created_on.date(), msg7.created_on.date())
        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        workbook = self._export(SystemLabel.TYPE_SENT, None, date(2000, 9, 1), date(2022, 9, 1))
        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        workbook = self._export(SystemLabel.TYPE_FAILED, None, date(2000, 9, 1), date(2022, 9, 1))
        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg4.created_on,
                    msg4.contact.uuid,
                    "Joe Blow",
                    "",
                    "",
                    "",
                    "OUT",
                    "hello 4",
                    "",
                    "failed",
                    "",
                    "",
                ],
                [
                    msg9.created_on,
                    msg9.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "OUT",
                    "Hey out 9",
                    "",
                    "failed",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        workbook = self._export(SystemLabel.TYPE_FLOWS, None, date(2000, 9, 1), date(2022, 9, 1))
        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg2.created_on,
                    msg2.contact.uuid,
                    "Frank Blow",
                    "tel",
                    "321",
                    "Color Flow",
                    "IN",
                    "hello 2",
                    "",
                    "handled",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        workbook = self._export(None, label, date(2000, 9, 1), date(2022, 9, 1))
        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                expected_headers,
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
            ],
            self.org.timezone,
        )

    def test_export(self):
        age = self.create_field("age", "Age")
        bob = self.create_contact("Bob", urns=["telegram:234567"], fields={"age": 40})
        devs = self.create_group("Devs", [bob])

        self.joe.name = "Jo\02e Blow"
        self.joe.save(update_fields=("name",))

        telegram = self.create_channel("TG", "Telegram", "765432")

        # messages can't be older than org
        self.org.created_on = datetime(2016, 1, 2, 10, tzinfo=tzone.utc)
        self.org.save(update_fields=("created_on",))

        flow = self.create_flow("Color Flow")
        msg1 = self.create_incoming_msg(
            self.joe, "hello 1", created_on=datetime(2017, 1, 1, 10, tzinfo=tzone.utc), flow=flow
        )
        msg2 = self.create_incoming_msg(
            bob, "hello 2", created_on=datetime(2017, 1, 2, 10, tzinfo=tzone.utc), channel=telegram
        )
        msg3 = self.create_incoming_msg(
            bob, "hello 3", created_on=datetime(2017, 1, 3, 10, tzinfo=tzone.utc), channel=telegram
        )

        # outbound message that doesn't have a channel or URN
        msg4 = self.create_outgoing_msg(
            self.joe,
            "hello 4",
            failed_reason=Msg.FAILED_NO_DESTINATION,
            created_on=datetime(2017, 1, 4, 10, tzinfo=tzone.utc),
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_incoming_msg(
            self.joe,
            "Media message",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=tzone.utc),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_outgoing_msg(
            self.joe, "Hey out 6", status=Msg.STATUS_SENT, created_on=datetime(2017, 1, 6, 10, tzinfo=tzone.utc)
        )
        msg7 = self.create_outgoing_msg(
            bob,
            "Hey out 7",
            status=Msg.STATUS_DELIVERED,
            created_on=datetime(2017, 1, 7, 10, tzinfo=tzone.utc),
            channel=telegram,
        )
        msg8 = self.create_outgoing_msg(
            self.joe, "Hey out 8", status=Msg.STATUS_ERRORED, created_on=datetime(2017, 1, 8, 10, tzinfo=tzone.utc)
        )
        msg9 = self.create_outgoing_msg(
            self.joe, "Hey out 9", status=Msg.STATUS_FAILED, created_on=datetime(2017, 1, 9, 10, tzinfo=tzone.utc)
        )

        self.assertEqual(msg5.get_attachments(), [Attachment("audio", "http://rapidpro.io/audio/sound.mp3")])

        # label first message
        label = self.create_label("la\02bel1")
        label.toggle_label([msg1], add=True)

        # archive last message
        msg3.visibility = Msg.VISIBILITY_ARCHIVED
        msg3.save()

        expected_headers = [
            "Date",
            "Contact UUID",
            "Contact Name",
            "URN Scheme",
            "URN Value",
            "Flow",
            "Direction",
            "Text",
            "Attachments",
            "Status",
            "Channel",
            "Labels",
        ]

        # export all visible messages (i.e. not msg3) using export_all param
        with self.assertNumQueries(16):
            self.assertExcelSheet(
                self._export(None, None, date(2000, 9, 1), date(2022, 9, 28)).worksheets[0],
                [
                    expected_headers,
                    [
                        msg1.created_on,
                        msg1.contact.uuid,
                        "Joe Blow",
                        "tel",
                        "123",
                        "Color Flow",
                        "IN",
                        "hello 1",
                        "",
                        "handled",
                        "Test Channel",
                        "label1",
                    ],
                    [
                        msg2.created_on,
                        msg2.contact.uuid,
                        "Bob",
                        "telegram",
                        "234567",
                        "",
                        "IN",
                        "hello 2",
                        "",
                        "handled",
                        "Telegram",
                        "",
                    ],
                    [
                        msg4.created_on,
                        msg4.contact.uuid,
                        "Joe Blow",
                        "",
                        "",
                        "",
                        "OUT",
                        "hello 4",
                        "",
                        "failed",
                        "",
                        "",
                    ],
                    [
                        msg5.created_on,
                        msg5.contact.uuid,
                        "Joe Blow",
                        "tel",
                        "123",
                        "",
                        "IN",
                        "Media message",
                        "http://rapidpro.io/audio/sound.mp3",
                        "handled",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg6.created_on,
                        msg6.contact.uuid,
                        "Joe Blow",
                        "tel",
                        "123",
                        "",
                        "OUT",
                        "Hey out 6",
                        "",
                        "sent",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg7.created_on,
                        msg7.contact.uuid,
                        "Bob",
                        "telegram",
                        "234567",
                        "",
                        "OUT",
                        "Hey out 7",
                        "",
                        "delivered",
                        "Telegram",
                        "",
                    ],
                    [
                        msg8.created_on,
                        msg8.contact.uuid,
                        "Joe Blow",
                        "tel",
                        "123",
                        "",
                        "OUT",
                        "Hey out 8",
                        "",
                        "errored",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg9.created_on,
                        msg9.contact.uuid,
                        "Joe Blow",
                        "tel",
                        "123",
                        "",
                        "OUT",
                        "Hey out 9",
                        "",
                        "failed",
                        "Test Channel",
                        "",
                    ],
                ],
                self.org.timezone,
            )

        # check that notifications were created
        export = Export.objects.filter(export_type=MessageExport.slug).order_by("id").last()
        self.assertEqual(
            1,
            self.admin.notifications.filter(
                notification_type="export:finished", export=export, email_status="P"
            ).count(),
        )

        # export just archived messages
        self.assertExcelSheet(
            self._export(SystemLabel.TYPE_ARCHIVED, None, date(2000, 9, 1), date(2022, 9, 28)).worksheets[0],
            [
                expected_headers,
                [
                    msg3.created_on,
                    msg3.contact.uuid,
                    "Bob",
                    "telegram",
                    "234567",
                    "",
                    "IN",
                    "hello 3",
                    "",
                    "handled",
                    "Telegram",
                    "",
                ],
            ],
            self.org.timezone,
        )

        # try export with user label
        self.assertExcelSheet(
            self._export(None, label, date(2000, 9, 1), date(2022, 9, 28)).worksheets[0],
            [
                expected_headers,
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "Color Flow",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
            ],
            self.org.timezone,
        )

        # try export with a date range, a field and a group
        self.assertExcelSheet(
            self._export(
                None, None, msg5.created_on.date(), msg7.created_on.date(), with_fields=[age], with_groups=[devs]
            ).worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Contact Name",
                    "URN Scheme",
                    "URN Value",
                    "Field:Age",
                    "Group:Devs",
                    "Flow",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    False,
                    "",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "tel",
                    "123",
                    "",
                    False,
                    "",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
                [
                    msg7.created_on,
                    msg7.contact.uuid,
                    "Bob",
                    "telegram",
                    "234567",
                    "40",
                    True,
                    "",
                    "OUT",
                    "Hey out 7",
                    "",
                    "delivered",
                    "Telegram",
                    "",
                ],
            ],
            self.org.timezone,
        )

        # test as anon org to check that URNs don't end up in exports
        with self.anonymous(self.org):
            self.assertExcelSheet(
                self._export(None, None, date(2000, 9, 1), date(2022, 9, 28)).worksheets[0],
                [
                    [
                        "Date",
                        "Contact UUID",
                        "Contact Name",
                        "URN Scheme",
                        "Anon Value",
                        "Flow",
                        "Direction",
                        "Text",
                        "Attachments",
                        "Status",
                        "Channel",
                        "Labels",
                    ],
                    [
                        msg1.created_on,
                        msg1.contact.uuid,
                        "Joe Blow",
                        "tel",
                        self.joe.anon_display,
                        "Color Flow",
                        "IN",
                        "hello 1",
                        "",
                        "handled",
                        "Test Channel",
                        "label1",
                    ],
                    [
                        msg2.created_on,
                        msg2.contact.uuid,
                        "Bob",
                        "telegram",
                        bob.anon_display,
                        "",
                        "IN",
                        "hello 2",
                        "",
                        "handled",
                        "Telegram",
                        "",
                    ],
                    [
                        msg4.created_on,
                        msg4.contact.uuid,
                        "Joe Blow",
                        "",
                        self.joe.anon_display,
                        "",
                        "OUT",
                        "hello 4",
                        "",
                        "failed",
                        "",
                        "",
                    ],
                    [
                        msg5.created_on,
                        msg5.contact.uuid,
                        "Joe Blow",
                        "tel",
                        self.joe.anon_display,
                        "",
                        "IN",
                        "Media message",
                        "http://rapidpro.io/audio/sound.mp3",
                        "handled",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg6.created_on,
                        msg6.contact.uuid,
                        "Joe Blow",
                        "tel",
                        self.joe.anon_display,
                        "",
                        "OUT",
                        "Hey out 6",
                        "",
                        "sent",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg7.created_on,
                        msg7.contact.uuid,
                        "Bob",
                        "telegram",
                        bob.anon_display,
                        "",
                        "OUT",
                        "Hey out 7",
                        "",
                        "delivered",
                        "Telegram",
                        "",
                    ],
                    [
                        msg8.created_on,
                        msg8.contact.uuid,
                        "Joe Blow",
                        "tel",
                        self.joe.anon_display,
                        "",
                        "OUT",
                        "Hey out 8",
                        "",
                        "errored",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg9.created_on,
                        msg9.contact.uuid,
                        "Joe Blow",
                        "tel",
                        self.joe.anon_display,
                        "",
                        "OUT",
                        "Hey out 9",
                        "",
                        "failed",
                        "Test Channel",
                        "",
                    ],
                ],
                self.org.timezone,
            )
