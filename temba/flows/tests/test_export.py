from datetime import timedelta
from unittest.mock import patch

from openpyxl import load_workbook

from django.core.files.storage import default_storage
from django.utils import timezone

from temba.archives.models import Archive
from temba.contacts.models import Contact, ContactURN
from temba.flows.models import Flow, FlowRun, ResultsExport
from temba.orgs.models import Export
from temba.tests import TembaTest, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.utils import json
from temba.utils.uuid import uuid4


class ResultsExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        self.contact3 = self.create_contact("Norbert", phone="+250788123456")

    def _export(
        self,
        flow,
        start_date,
        end_date,
        responded_only=False,
        with_fields=(),
        with_groups=(),
        extra_urns=(),
        has_results=True,
    ):
        """
        Exports results for the given flow and returns the generated workbook
        """

        readonly_models = {FlowRun}
        if has_results:
            readonly_models.add(Contact)
            readonly_models.add(ContactURN)

        export = ResultsExport.create(
            self.org,
            self.admin,
            start_date,
            end_date,
            flows=[flow],
            with_fields=with_fields,
            with_groups=with_groups,
            responded_only=responded_only,
            extra_urns=extra_urns,
        )

        with self.mockReadOnly(assert_models=readonly_models):
            export.perform()

        return load_workbook(filename=default_storage.open(f"orgs/{self.org.id}/results_exports/{export.uuid}.xlsx"))

    @mock_mailroom
    def test_export(self, mr_mocks):
        today = timezone.now().astimezone(self.org.timezone).date()

        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]
        orange_reply = flow_nodes[1]

        # add a spec for a hidden result to this flow
        flow.metadata[Flow.METADATA_RESULTS].append(
            {
                "key": "_color_classification",
                "name": "_Color Classification",
                "categories": ["Success", "Skipped", "Failure"],
                "node_uuids": [color_split["uuid"]],
            }
        )

        age = self.create_field("age", "Age")
        devs = self.create_group("Devs", [self.contact])

        mods = self.contact.update_fields({age: "36"})
        mods += self.contact.update_urns(["tel:+250788382382", "twitter:erictweets"])
        self.contact.modify(self.admin, mods)

        # contact name with an illegal character
        self.contact3.name = "Nor\02bert"
        self.contact3.save(update_fields=("name",))

        contact3_run1 = (
            MockSessionWriter(self.contact3, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in1 = self.create_incoming_msg(self.contact, "light beige")
        contact1_in2 = self.create_incoming_msg(self.contact, "orange")
        contact1_run1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "light beige", "Other", "light beige")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in2)
            .set_result("Color", "orange", "Orange", "orange")
            .set_result("_Color Classification", "orange", "Success", "color_selection")  # hidden result
            .visit(orange_reply)
            .send_msg(
                "I love orange too! You said: orange which is category: Orange You are: 0788 382 382 SMS: orange Flow: color: orange",
                self.channel,
            )
            .complete()
            .save()
        ).session.runs.get()

        contact2_in1 = self.create_incoming_msg(self.contact2, "green")
        contact2_run1 = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_run2 = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in3 = self.create_incoming_msg(self.contact, " blue ")
        contact1_run2 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in3)
            .set_result("Color", "blue", "Blue", " blue ")
            .visit(orange_reply)
            .send_msg("Blue is sad. :(", self.channel)
            .complete()
            .save()
        ).session.runs.get()

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        with self.assertNumQueries(23):
            workbook = self._export(
                flow,
                start_date=today - timedelta(days=7),
                end_date=today,
                with_groups=[devs],
            )

        # check that notifications were created
        export = Export.objects.filter(export_type=ResultsExport.slug).order_by("id").last()
        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", export=export).count())

        tz = self.org.timezone

        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(12, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "Contact Name",
                "URN Scheme",
                "URN Value",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact3_run1.contact.uuid,
                "Norbert",
                "tel",
                "+250788123456",
                False,
                contact3_run1.created_on,
                contact3_run1.modified_on,
                "",
                contact3_run1.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact1_run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact2_run1.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact2_run2.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                False,
                contact2_run2.created_on,
                contact2_run2.modified_on,
                "",
                contact2_run2.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact1_run2.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                True,
                contact1_run2.created_on,
                contact1_run2.modified_on,
                contact1_run2.exited_on,
                contact1_run2.uuid,
                "Blue",
                "blue",
                " blue ",
            ],
            tz,
        )

        # test without unresponded
        with self.assertNumQueries(21):
            workbook = self._export(
                flow,
                start_date=today - timedelta(days=7),
                end_date=today,
                responded_only=True,
                with_groups=(devs,),
            )

        tz = self.org.timezone
        sheet_runs = workbook.worksheets[0]

        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs
        self.assertEqual(12, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "Contact Name",
                "URN Scheme",
                "URN Value",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run1.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )

        # test export with a contact field
        with self.assertNumQueries(25):
            workbook = self._export(
                flow,
                start_date=today - timedelta(days=7),
                end_date=today,
                with_fields=[age],
                with_groups=[devs],
                responded_only=True,
                extra_urns=["twitter", "line"],
            )

        tz = self.org.timezone
        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs
        self.assertEqual(15, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "Contact Name",
                "URN Scheme",
                "URN Value",
                "Field:Age",
                "Group:Devs",
                "URN:Twitter",
                "URN:Line",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                "36",
                True,
                "erictweets",
                "",
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        # test that we don't exceed the limit on rows per sheet
        with patch("temba.utils.export.MultiSheetExporter.MAX_EXCEL_ROWS", 4):
            workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)
            expected_sheets = [("Runs 1", 4), ("Runs 2", 3)]

            for s, sheet in enumerate(workbook.worksheets):
                self.assertEqual((sheet.title, len(list(sheet.rows))), expected_sheets[s])

        # test we can export archived flows
        flow.is_archived = True
        flow.save()

        workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)

        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(11, len(list(sheet_runs.columns)))

    def test_anon_org(self):
        today = timezone.now().astimezone(self.org.timezone).date()

        with self.anonymous(self.org):
            flow = self.get_flow("color_v13")
            flow_nodes = flow.get_definition()["nodes"]
            color_prompt = flow_nodes[0]
            color_split = flow_nodes[4]

            msg_in = self.create_incoming_msg(self.contact, "orange")

            run1 = (
                MockSessionWriter(self.contact, flow)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=msg_in)
                .set_result("Color", "orange", "Orange", "orange")
                .send_msg("I love orange too!", self.channel)
                .complete()
                .save()
            ).session.runs.get()

            workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)
            self.assertEqual(1, len(workbook.worksheets))
            sheet_runs = workbook.worksheets[0]
            self.assertExcelRow(
                sheet_runs,
                0,
                [
                    "Contact UUID",
                    "Contact Name",
                    "URN Scheme",
                    "Anon Value",
                    "Started",
                    "Modified",
                    "Exited",
                    "Run UUID",
                    "Color (Category) - Colors",
                    "Color (Value) - Colors",
                    "Color (Text) - Colors",
                ],
            )

            self.assertExcelRow(
                sheet_runs,
                1,
                [
                    self.contact.uuid,
                    "Eric",
                    "tel",
                    self.contact.anon_display,
                    run1.created_on,
                    run1.modified_on,
                    run1.exited_on,
                    run1.uuid,
                    "Orange",
                    "orange",
                    "orange",
                ],
                self.org.timezone,
            )

    def test_broadcast_only_flow(self):
        flow = self.get_flow("send_only_v13")
        send_node = flow.get_definition()["nodes"][0]
        today = timezone.now().astimezone(self.org.timezone).date()

        for contact in [self.contact, self.contact2, self.contact3]:
            (
                MockSessionWriter(contact, flow)
                .visit(send_node)
                .send_msg("This is the first message.", self.channel)
                .send_msg("This is the second message.", self.channel)
                .complete()
                .save()
            ).session.runs.get()

        for contact in [self.contact, self.contact2]:
            (
                MockSessionWriter(contact, flow)
                .visit(send_node)
                .send_msg("This is the first message.", self.channel)
                .send_msg("This is the second message.", self.channel)
                .complete()
                .save()
            ).session.runs.get()

        contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2 = FlowRun.objects.order_by("id")

        with self.assertNumQueries(17):
            workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)

        tz = self.org.timezone

        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(8, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            ["Contact UUID", "Contact Name", "URN Scheme", "URN Value", "Started", "Modified", "Exited", "Run UUID"],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run1.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact3_run1.contact.uuid,
                "Norbert",
                "tel",
                "+250788123456",
                contact3_run1.created_on,
                contact3_run1.modified_on,
                contact3_run1.exited_on,
                contact3_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact1_run2.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                contact1_run2.created_on,
                contact1_run2.modified_on,
                contact1_run2.exited_on,
                contact1_run2.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact2_run2.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                contact2_run2.created_on,
                contact2_run2.modified_on,
                contact2_run2.exited_on,
                contact2_run2.uuid,
            ],
            tz,
        )

        # test without unresponded
        with self.assertNumQueries(10):
            workbook = self._export(
                flow,
                start_date=today - timedelta(days=7),
                end_date=today,
                responded_only=True,
                has_results=False,
            )

        (sheet_runs,) = workbook.worksheets

        self.assertEqual(1, len(list(sheet_runs.rows)), 1)  # header; no resposes to a broadcast only flow
        self.assertEqual(8, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            ["Contact UUID", "Contact Name", "URN Scheme", "URN Value", "Started", "Modified", "Exited", "Run UUID"],
        )

    def test_replaced_rulesets(self):
        today = timezone.now().astimezone(self.org.timezone).date()

        favorites = self.get_flow("favorites_v13")
        flow_json = favorites.get_definition()
        flow_nodes = flow_json["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        contact3_run1 = (
            MockSessionWriter(self.contact3, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in1 = self.create_incoming_msg(self.contact, "light beige")
        contact1_in2 = self.create_incoming_msg(self.contact, "red")
        contact1_run1 = (
            MockSessionWriter(self.contact, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "light beige", "Other", "light beige")
            .visit(color_other)
            .send_msg("I don't know that color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
            .resume(msg=contact1_in2)
            .set_result("Color", "red", "Red", "red")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .complete()
            .save()
        ).session.runs.get()

        devs = self.create_group("Devs", [self.contact])

        # now remap the uuid for our color
        flow_json = json.loads(json.dumps(flow_json).replace(color_split["uuid"], str(uuid4())))
        favorites.save_revision(self.admin, flow_json)
        flow_nodes = flow_json["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]

        contact2_in1 = self.create_incoming_msg(self.contact2, "green")
        contact2_run1 = (
            MockSessionWriter(self.contact2, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "green", "Green", "green")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Green too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_run2 = (
            MockSessionWriter(self.contact2, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in3 = self.create_incoming_msg(self.contact, " blue ")
        contact1_run2 = (
            MockSessionWriter(self.contact, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in3)
            .set_result("Color", "blue", "Blue", " blue ")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .save()
        ).session.runs.get()

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        workbook = self._export(favorites, start_date=today - timedelta(days=7), end_date=today, with_groups=[devs])

        tz = self.org.timezone

        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(18, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "Contact Name",
                "URN Scheme",
                "URN Value",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Favorites",
                "Color (Value) - Favorites",
                "Color (Text) - Favorites",
                "Beer (Category) - Favorites",
                "Beer (Value) - Favorites",
                "Beer (Text) - Favorites",
                "Name (Category) - Favorites",
                "Name (Value) - Favorites",
                "Name (Text) - Favorites",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact3_run1.contact.uuid,
                "Norbert",
                "tel",
                "+250788123456",
                False,
                contact3_run1.created_on,
                contact3_run1.modified_on,
                "",
                contact3_run1.uuid,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact1_run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Red",
                "red",
                "red",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact2_run1.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Green",
                "green",
                "green",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact2_run2.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                False,
                contact2_run2.created_on,
                contact2_run2.modified_on,
                "",
                contact2_run2.uuid,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact1_run2.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                True,
                contact1_run2.created_on,
                contact1_run2.modified_on,
                "",
                contact1_run2.uuid,
                "Blue",
                "blue",
                " blue ",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

    def test_remove_control_characters(self):
        today = timezone.now().astimezone(self.org.timezone).date()

        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]

        msg_in = self.create_incoming_msg(self.contact, "ngert\x07in.")

        run1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg_in)
            .set_result("Color", "ngert\x07in.", "Other", "ngert\x07in.")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)
        tz = self.org.timezone
        (sheet_runs,) = workbook.worksheets

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                run1.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                run1.created_on,
                run1.modified_on,
                "",
                run1.uuid,
                "Other",
                "ngertin.",
                "ngertin.",
            ],
            tz,
        )

    def test_from_archives(self):
        today = timezone.now().astimezone(self.org.timezone).date()

        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]
        blue_reply = flow_nodes[2]

        contact1_in1 = self.create_incoming_msg(self.contact, "green")
        contact1_run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_in1 = self.create_incoming_msg(self.contact2, "blue")
        contact2_run = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "blue", "Blue", "blue")
            .visit(blue_reply)
            .send_msg("Blue is sad :(.", self.channel)
            .complete()
            .save()
        ).session.runs.get()

        # and a run for a different flow
        flow2 = self.get_flow("favorites_v13")
        flow2_nodes = flow2.get_definition()["nodes"]

        contact2_other_flow = (
            MockSessionWriter(self.contact2, flow2)
            .visit(flow2_nodes[0])
            .send_msg("Color???", self.channel)
            .visit(flow2_nodes[2])
            .wait()
            .save()
        ).session.runs.get()

        contact3_run = (
            MockSessionWriter(self.contact3, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        # we now have 4 runs in this order of modified_on
        contact1_run.refresh_from_db()
        contact2_run.refresh_from_db()
        contact2_other_flow.refresh_from_db()
        contact3_run.refresh_from_db()

        # archive the first 3 runs, using 'old' archive format that used a list of values for one of them
        old_archive_format = contact2_run.as_archive_json()
        old_archive_format["values"] = [old_archive_format["values"]]

        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            timezone.now().date(),
            [contact1_run.as_archive_json(), old_archive_format, contact2_other_flow.as_archive_json()],
        )

        contact1_run.delete()
        contact2_run.delete()

        # create an archive earlier than our flow created date so we check that it isn't included
        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            timezone.now().date() - timedelta(days=2),
            [contact2_run.as_archive_json()],
        )

        workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today)

        tz = self.org.timezone
        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run.contact.uuid,
                "Eric",
                "tel",
                "+250788382382",
                contact1_run.created_on,
                contact1_run.modified_on,
                "",
                contact1_run.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run.contact.uuid,
                "Nic",
                "tel",
                "+250788383383",
                contact2_run.created_on,
                contact2_run.modified_on,
                contact2_run.exited_on,
                contact2_run.uuid,
                "Blue",
                "blue",
                "blue",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact3_run.contact.uuid,
                "Norbert",
                "tel",
                "+250788123456",
                contact3_run.created_on,
                contact3_run.modified_on,
                "",
                contact3_run.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

    def test_no_responses(self):
        today = timezone.now().astimezone(self.org.timezone).date()
        flow = self.create_flow("Test")

        self.assertEqual(flow.get_run_stats()["total"], 0)

        workbook = self._export(flow, start_date=today - timedelta(days=7), end_date=today, has_results=False)

        self.assertEqual(len(workbook.worksheets), 1)

        # every sheet has only the head row
        self.assertEqual(1, len(list(workbook.worksheets[0].rows)))
        self.assertEqual(8, len(list(workbook.worksheets[0].columns)))
