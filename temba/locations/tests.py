import os
import shutil
import tempfile
from unittest.mock import Mock, mock_open, patch

import responses

from django.core.management import call_command
from django.test.utils import captured_stdout
from django.urls import reverse

from temba.tests import TembaTest
from temba.utils import json

from .models import AdminBoundary, BoundaryAlias


class LocationTest(TembaTest):
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
        self.assertRedirect(response, reverse("orgs.org_home"))

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

        # update our alias for kigali
        with self.assertNumQueries(17):
            response = self.client.post(
                reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
                json.dumps(dict(osm_id=self.state1.osm_id, aliases="kigs\nkig")),
                content_type="application/json",
            )

        self.assertEqual(200, response.status_code)

        # fetch our aliases again
        with self.assertNumQueries(19):
            response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        children = response_json[0]["children"]
        self.assertEqual("Kigali City", children[1]["name"])
        self.assertEqual("kig\nkigs", children[1]["aliases"])

        # query for our alias
        search_result = self.client.get(
            f"{reverse('locations.adminboundary_boundaries', args=[self.country.osm_id])}?q=kigs"
        )
        self.assertEqual("Kigali City", search_result.json()[0]["name"])

        # update our alias for kigali with duplicates
        with self.assertNumQueries(17):
            response = self.client.post(
                reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]),
                json.dumps(dict(osm_id=self.state1.osm_id, aliases="kigs\nkig\nkig\nkigs\nkig")),
                content_type="application/json",
            )

        self.assertEqual(200, response.status_code)

        self.setUpSecondaryOrg()
        BoundaryAlias.objects.create(
            boundary=self.state1, org=self.org2, name="KGL", created_by=self.admin2, modified_by=self.admin2
        )

        # fetch our aliases again
        with self.assertNumQueries(19):
            response = self.client.get(reverse("locations.adminboundary_boundaries", args=[self.country.osm_id]))
        response_json = response.json()

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

        # exact match
        boundary = self.org.find_boundary_by_name("kigali city", AdminBoundary.LEVEL_STATE, self.country)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # try to find the location by alias
        boundary = self.org.find_boundary_by_name("kigs", AdminBoundary.LEVEL_STATE, self.country)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # also try with no parent
        boundary = self.org.find_boundary_by_name("kigs", AdminBoundary.LEVEL_STATE, None)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # test no match
        boundary = self.org.find_boundary_by_name("foobar", AdminBoundary.LEVEL_STATE, None)
        self.assertFalse(boundary)

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

    def test_adminboundary_create(self):
        # create a simple boundary
        boundary = AdminBoundary.create(osm_id="-1", name="Null Island", level=0)
        self.assertEqual(boundary.path, "Null Island")
        self.assertIsNone(boundary.simplified_geometry)

        # create a simple boundary with parent
        child_boundary = AdminBoundary.create(osm_id="-2", name="Palm Tree", level=1, parent=boundary)
        self.assertEqual(child_boundary.path, "Null Island > Palm Tree")
        self.assertIsNone(child_boundary.geometry)

        wkb_geometry = (
            "0106000000010000000103000000010000000400000000000000407241C01395356EBA0B304000000000602640C0CDC2B7C4027A27"
            "400000000080443DC040848F2D272C304000000000407241C01395356EBA0B3040"
        )

        # create a simple boundary with parent and geometry
        geom_boundary = AdminBoundary.create(
            osm_id="-3",
            name="Plum Tree",
            level=1,
            parent=boundary,
            simplified_geometry=wkb_geometry,
            geometry=wkb_geometry,
        )
        self.assertEqual(geom_boundary.path, "Null Island > Plum Tree")
        self.assertIsNotNone(geom_boundary.simplified_geometry)
        self.assertIsNotNone(geom_boundary.geometry)

        # path should not be defined when calling AdminBoundary.create
        self.assertRaises(TypeError, AdminBoundary.create, osm_id="-1", name="Null Island", level=0, path="some path")


class ImportGeoJSONtest(TembaTest):
    data_geojson_level_0 = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R1000",
                    "name": "Međa",
                    "name_en": "Granica",
                    "is_in_country": "None",
                    "is_in_state": "None"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                }
            }]
        }"""

    data_geojson_level_1 = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R2000",
                    "name": "Međa 2",
                    "name_en": "Granica 2",
                    "is_in_country": "R1000",
                    "is_in_state": "None"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                }
            }]
        }"""

    data_geojson_level_1_new_boundary = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R3000",
                    "name": "Međa 3",
                    "name_en": "Granica 3",
                    "is_in_country": "R1000",
                    "is_in_state": "None"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                }
            }]
        }"""

    data_geojson_level_2 = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R55000",
                    "name": "Međa 55",
                    "name_en": "Granica 55",
                    "is_in_country": "R1000",
                    "is_in_state": "R2000"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                }
            }]
        }"""

    data_geojson_without_parent = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R2000",
                    "name": "Međa",
                    "name_en": "Granica",
                    "is_in_country": "R0"
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                }
            }]
        }"""

    data_geojson_feature_no_geometry = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R1000",
                    "name": "Međa",
                    "name_en": "Granica"
                },
                "geometry": {}
            }]
        }"""

    data_geojson_multipolygon = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R1000",
                    "name": "Međa",
                    "name_en": "Granica"
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[1, 1], [2, 2], [1, 3], [1, 1]]],
                        [[[1, 1], [2, 2], [1, 3], [1, 1]]]
                    ]
                }
            }]
        }"""

    data_geojson_unknown_geometry = """{
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_id": "R1000",
                    "name": "Međa",
                    "name_en": "Granica"
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[1, 1], [2, 2], [1, 3], [1, 1]]
                }
            }]
        }"""

    data_geojson_no_features = """{
            "type": "FeatureCollection",
            "features": []
        }"""

    def test_wrong_filename(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "data.json")

        self.assertEqual(
            captured_output.getvalue(), "=== parsing data.json\nSkipping 'data.json', doesn't match file pattern.\n"
        )

        self.assertEqual(AdminBoundary.objects.count(), 0)

    def test_filename_with_no_features(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_no_features)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "R188933admin0.json")

        self.assertEqual(captured_output.getvalue(), "=== parsing R188933admin0.json\n")

        self.assertEqual(AdminBoundary.objects.count(), 0)

    def test_ok_filename_admin(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "R188933admin0.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing R188933admin0.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertEqual(AdminBoundary.objects.count(), 1)

    def test_ok_filename_admin_level_with_country_prefix(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "R188933admin0.json", "--country=R188933")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing R188933admin0.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertEqual(AdminBoundary.objects.count(), 1)

    def test_ok_filename_admin_level(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertEqual(AdminBoundary.objects.count(), 1)

    def test_missing_parent_in_db(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_without_parent)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_1_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_1_simplified.json\nSkipping Međa (R2000) as parent R0 not found.\n",
        )

        self.assertEqual(AdminBoundary.objects.count(), 0)

    def test_feature_without_geometry(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_feature_no_geometry)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json")

        self.assertEqual(captured_output.getvalue(), "=== parsing admin_level_0_simplified.json\n")

        self.assertEqual(AdminBoundary.objects.count(), 0)

    def test_feature_multipolygon_geometry(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_multipolygon)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000"})

    def test_feature_unknown_geometry(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_unknown_geometry)):
            self.assertRaises(Exception, call_command, "import_geojson", "admin_level_0_simplified.json")

    def test_feature_with_parent(self):
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1, self.data_geojson_level_2]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout() as captured_output:
                call_command(
                    "import_geojson",
                    "admin_level_0_simplified.json",
                    "admin_level_1_simplified.json",
                    "admin_level_2_simplified.json",
                )

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\n=== parsing admin_level_1_simplified.json\n ** adding Međa 2 (R2000)\n ** removing unseen boundaries (R2000)\n=== parsing admin_level_2_simplified.json\n ** adding Međa 55 (R55000)\n ** removing unseen boundaries (R55000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000", "R2000", "R55000"})

    def test_update_features_with_parent(self):
        # insert features in the database
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout():
                call_command("import_geojson", "admin_level_0_simplified.json", "admin_level_1_simplified.json")

        self.assertOSMIDs({"R1000", "R2000"})

        # update features
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json", "admin_level_1_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** updating Granica (R1000)\n ** removing unseen boundaries (R1000)\n=== parsing admin_level_1_simplified.json\n ** updating Međa 2 (R2000)\n ** removing unseen boundaries (R2000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000", "R2000"})

    def test_remove_unseen_boundaries(self):
        # insert features in the database
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout():
                call_command("import_geojson", "admin_level_0_simplified.json", "admin_level_1_simplified.json")

        self.assertOSMIDs({"R1000", "R2000"})

        BoundaryAlias.create(self.org, self.admin, AdminBoundary.objects.get(osm_id="R2000"), "My Alias")

        # update data, and add a new boundary
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1_new_boundary]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json", "admin_level_1_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** updating Granica (R1000)\n ** removing unseen boundaries (R1000)\n=== parsing admin_level_1_simplified.json\n ** adding Međa 3 (R3000)\n ** removing unseen boundaries (R3000)\n ** Unseen boundaries removed: 1\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000", "R3000"})

    def test_remove_other_unseen_boundaries(self):
        # other unseen boundaries are boundaries which have not been updated in any way for a country

        # insert features in the database
        geojson_data = [self.data_geojson_level_0, self.data_geojson_level_1]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout():
                call_command("import_geojson", "admin_level_0_simplified.json", "admin_level_1_simplified.json")

        self.assertOSMIDs({"R1000", "R2000"})

        # update data, and add a new boundary
        geojson_data = [self.data_geojson_level_0]

        with patch("builtins.open") as mock_file:
            mock_file.return_value.__enter__ = lambda filename: filename
            mock_file.return_value.__exit__ = Mock()
            mock_file.return_value.read.side_effect = lambda: geojson_data.pop(0)

            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** updating Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 1\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000"})

    def test_zipfiles_parsing(self):
        with patch("temba.locations.management.commands.import_geojson.ZipFile") as zipfile_patched:
            zipfile_patched().namelist.return_value = ["admin_level_0_simplified.json"]

            zipfile_patched().open.return_value.__enter__ = lambda filename: filename
            zipfile_patched().open.return_value.__exit__ = Mock()
            zipfile_patched().open.return_value.read.return_value = self.data_geojson_level_0

            with captured_stdout() as captured_output:
                call_command("import_geojson", "admin_level_0_simplified.zip")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing admin_level_0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertOSMIDs({"R1000"})

    def assertOSMIDs(self, ids):
        self.assertEqual(set(ids), set(AdminBoundary.objects.values_list("osm_id", flat=True)))


class DownloadGeoJsonTest(TembaTest):
    def setUp(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nyaruka/posm-extracts/git/trees/master",
            body=json.dumps({"tree": [{"path": "geojson", "sha": "the-sha"}]}),
            content_type="application/json",
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nyaruka/posm-extracts/git/trees/the-sha",
            body=json.dumps({"tree": [{"path": "R12345_simplified.json"}, {"path": "R45678_simplified.json"}]}),
            content_type="application/json",
        )
        responses.add(
            responses.GET,
            "https://raw.githubusercontent.com/nyaruka/posm-extracts/master/geojson/R12345_simplified.json",
            body="the-relation-json",
            content_type="application/json",
        )
        self.testdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.testdir)

    @responses.activate
    def test_download_geojson(self):
        destination_dir = os.path.join(self.testdir, "geojson")
        good_path = os.path.join(destination_dir, "R12345_simplified.json")
        bad_path = os.path.join(destination_dir, "R45678_simplified.json")
        call_command("download_geojson", "12345", "--dir", destination_dir)
        self.assertFalse(os.path.exists(bad_path))
        self.assertTrue(os.path.exists(good_path))
        with open(good_path, "r") as fp:
            self.assertEqual(fp.read(), "the-relation-json")
