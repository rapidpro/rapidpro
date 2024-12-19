from django.urls import reverse

from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.tests import TembaTest
from temba.utils import json


class LocationTest(TembaTest):
    def test_aliases_update(self):
        self.setUpLocations()

        # make other workspace with the same locations
        self.org2.country = self.country
        self.org2.save(update_fields=("country",))

        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org).count(), 1)
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org).get().name, "Kigari")
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org2).count(), 1)
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org2).get().name, "Chigali")

        self.state1.update_aliases(self.org, self.admin, ["Kigari", "CapitalCity", "MVK"])
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org).count(), 3)
        self.assertEqual(
            list(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org).values_list("name", flat=True)),
            ["Kigari", "CapitalCity", "MVK"],
        )
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org2).get().name, "Chigali")

        self.state1.update_aliases(self.org2, self.admin2, ["Chigali", "CapitalCity", "MVK"])
        self.assertEqual(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org2).count(), 3)
        self.assertEqual(
            list(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org2).values_list("name", flat=True)),
            ["Chigali", "CapitalCity", "MVK"],
        )
        self.assertEqual(
            list(BoundaryAlias.objects.filter(boundary=self.state1, org=self.org).values_list("name", flat=True)),
            ["Kigari", "CapitalCity", "MVK"],
        )

    def test_boundaries(self):
        self.setUpLocations()

        self.login(self.admin)

        # clear our country on our org
        self.org.country = None
        self.org.save()

        # try stripping path on our country, will fail
        with self.assertRaises(Exception):
            AdminBoundary.strip_last_path("Rwanda")

        # normal strip
        self.assertEqual(AdminBoundary.strip_last_path("Rwanda > Kigali City"), "Rwanda")

        # get the aliases for our user org
        response = self.client.get(reverse("locations.adminboundary_alias"))

        # should be a redirect to our org home
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        # now set it to rwanda
        self.org.country = self.country
        self.org.save()
        # our country is set to rwanda, we should get it as the main object
        response = self.client.get(reverse("locations.adminboundary_alias"))
        self.assertEqual(self.country, response.context["object"])

        # ok, now get the geometry for rwanda
        response = self.client.get(reverse("locations.adminboundary_geometry", args=[self.country.osm_id]))

        # should be json
        response_json = response.json()
        geometry = response_json["geometry"]

        # should have features in it
        self.assertIn("features", geometry)

        # should have our two top level states
        self.assertEqual(2, len(geometry["features"]))
        # now get it for one of the sub areas
        response = self.client.get(reverse("locations.adminboundary_geometry", args=[self.district1.osm_id]))
        response_json = response.json()
        geometry = response_json["geometry"]

        # should have features in it
        self.assertIn("features", geometry)

        # should have our single district in it
        self.assertEqual(1, len(geometry["features"]))

        # now grab our aliases
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        self.assertEqual(
            [
                {
                    "osm_id": "171496",
                    "name": "Rwanda",
                    "level": 0,
                    "aliases": "",
                    "path": "Rwanda",
                    "children": [
                        {
                            "osm_id": "171591",
                            "name": "Eastern Province",
                            "level": 1,
                            "aliases": "",
                            "path": "Rwanda > Eastern Province",
                            "parent_osm_id": "171496",
                            "has_children": True,
                        },
                        {
                            "osm_id": "1708283",
                            "name": "Kigali City",
                            "level": 1,
                            "aliases": "Kigari",
                            "path": "Rwanda > Kigali City",
                            "parent_osm_id": "171496",
                            "has_children": True,
                        },
                    ],
                    "has_children": True,
                }
            ],
            response_json,
        )

        # update our alias for east
        with self.assertNumQueries(13):
            response = self.client.post(
                reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
                json.dumps(dict(osm_id=self.state2.osm_id, aliases="kigs\n")),
                content_type="application/json",
            )

        self.assertEqual(200, response.status_code)

        # fetch our aliases
        with self.assertNumQueries(18):
            response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        children = response_json[0]["children"]
        self.assertEqual("Eastern Province", children[0]["name"])
        self.assertEqual("kigs", children[0]["aliases"])

        # update our alias for Nyarugenge
        response = self.client.post(
            reverse("locations.adminboundary_boundaries", args=[self.state1.osm_id]),
            json.dumps(dict(osm_id=self.district3.osm_id, aliases="kigs\n")),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)

        # fetch our aliases
        with self.assertNumQueries(25):
            response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.state1.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        children = response_json[1]["children"]
        self.assertEqual("Nyarugenge", children[0]["name"])
        self.assertEqual("kigs", children[0]["aliases"])

        # update our alias for kigali
        response = self.client.post(
            reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
            json.dumps(dict(osm_id=self.state1.osm_id, aliases="kigs\nkig")),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)

        # fetch our aliases
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.state1.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        children = response_json[1]["children"]
        self.assertEqual("Nyarugenge", children[0]["name"])
        self.assertEqual("kigs", children[0]["aliases"])

        # fetch our aliases again
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        children = response_json[0]["children"]
        self.assertEqual("Kigali City", children[1]["name"])
        self.assertEqual("kig\nkigs", children[1]["aliases"])
        self.assertEqual(
            "", children[0]["aliases"]
        )  # kigs alias should have been moved from the eastern province boundary

        # fetch our aliases
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.state1.osm_id]))
        response_json = response.json()

        # now have kigs still as an alias on Nyarugenge
        children = response_json[1]["children"]
        self.assertEqual("Nyarugenge", children[0]["name"])
        self.assertEqual("kigs", children[0]["aliases"])

        # query for our alias
        search_result = self.client.get(
            f"{reverse('locations.adminboundary_boundaries', args=[self.country.osm_id])}?q=kigs"
        )
        self.assertEqual("Kigali City", search_result.json()[0]["name"])

        # update our alias for kigali with duplicates
        response = self.client.post(
            reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
            json.dumps(dict(osm_id=self.state1.osm_id, aliases="kigs\nkig\nkig\nkigs\nkig")),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)

        BoundaryAlias.objects.create(
            boundary=self.state1, org=self.org2, name="KGL", created_by=self.admin2, modified_by=self.admin2
        )

        # fetch our aliases again
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        self.assertEqual(response_json[0]["aliases"], "")

        # now have kigs as an alias
        children = response_json[0]["children"]
        self.assertEqual("Kigali City", children[1]["name"])
        self.assertEqual("kig\nkigs", children[1]["aliases"])

        # test nested admin level aliases update
        geo_data = dict(
            osm_id=self.state2.osm_id,
            aliases="Eastern P",
            children=[
                dict(
                    osm_id=self.district1.osm_id,
                    aliases="Gatsibo",
                    children=[dict(osm_id=self.ward1.osm_id, aliases="Kageyo Gat")],
                )
            ],
        )

        response = self.client.post(
            reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
            json.dumps(geo_data),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)

        BoundaryAlias.objects.create(
            boundary=self.country, org=self.org2, name="SameRwanda", created_by=self.admin2, modified_by=self.admin2
        )
        BoundaryAlias.objects.create(
            boundary=self.country, org=self.org, name="MyRwanda", created_by=self.admin2, modified_by=self.admin2
        )

        # fetch our aliases again
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        self.assertEqual(response_json[0]["aliases"], "MyRwanda")

        # fetch aliases again
        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()
        children = response_json[0]["children"]
        self.assertEqual(children[0].get("name"), self.state2.name)
        self.assertEqual(children[0].get("aliases"), "Eastern P")

        # trigger wrong request data using bad json
        response = self.client.post(
            reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
            """{"data":"foo \r\n bar"}""",
            content_type="application/json",
        )

        response_json = response.json()
        self.assertEqual(400, response.status_code)
        self.assertEqual(response_json.get("status"), "error")

        # Get geometry of admin boundary without sub-levels, should return one feature
        response = self.client.get(reverse("locations.adminboundary_geometry", args=[self.ward3.osm_id]))
        self.assertEqual(200, response.status_code)
        response_json = response.json()
        self.assertEqual(len(response_json.get("geometry").get("features")), 1)

        long_number_ids = AdminBoundary.create(osm_id="SOME.123.12_12", name="Gatsibo", level=2, parent=self.state2)

        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[long_number_ids.osm_id]))
        self.assertEqual(200, response.status_code)

        word_only_ids = AdminBoundary.create(osm_id="SOME", name="Gatsibo", level=2, parent=self.state2)

        response = self.client.get(reverse("locations.adminboundary_boundaries", args=[word_only_ids.osm_id]))
        self.assertEqual(200, response.status_code)

    def test_adminboundary_create(self):
        # create a simple boundary
        boundary = AdminBoundary.create(osm_id="-1", name="Null Island", level=0)
        self.assertEqual(boundary.path, "Null Island")
        self.assertIsNone(boundary.simplified_geometry)

        # create a simple boundary with parent
        child_boundary = AdminBoundary.create(osm_id="-2", name="Palm Tree", level=1, parent=boundary)
        self.assertEqual(child_boundary.path, "Null Island > Palm Tree")
        self.assertIsNone(child_boundary.simplified_geometry)

        wkb_geometry = (
            "0106000000010000000103000000010000000400000000000000407241C01395356EBA0B304000000000602640C0CDC2B7C4027A27"
            "400000000080443DC040848F2D272C304000000000407241C01395356EBA0B3040"
        )

        # create a simple boundary with parent and geometry
        geom_boundary = AdminBoundary.create(
            osm_id="-3", name="Plum Tree", level=1, parent=boundary, simplified_geometry=wkb_geometry
        )
        self.assertEqual(geom_boundary.path, "Null Island > Plum Tree")
        self.assertIsNotNone(geom_boundary.simplified_geometry)

        # path should not be defined when calling AdminBoundary.create
        self.assertRaises(TypeError, AdminBoundary.create, osm_id="-1", name="Null Island", level=0, path="some path")
