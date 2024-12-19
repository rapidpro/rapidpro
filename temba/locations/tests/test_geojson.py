import os
import shutil
import tempfile
from unittest.mock import Mock, mock_open, patch

import responses

from django.core.management import call_command
from django.test.utils import captured_stdout

from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.tests import TembaTest
from temba.utils import json


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
                call_command("import_geojson", "R188933admin0_simplified.json")

        self.assertEqual(captured_output.getvalue(), "=== parsing R188933admin0_simplified.json\n")

        self.assertEqual(AdminBoundary.objects.count(), 0)

    def test_ok_filename_admin(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "R188933admin0_simplified.json")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing R188933admin0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
        )

        self.assertEqual(AdminBoundary.objects.count(), 1)

    def test_ok_filename_admin_level_with_country_prefix(self):
        with patch("builtins.open", mock_open(read_data=self.data_geojson_level_0)):
            with captured_stdout() as captured_output:
                call_command("import_geojson", "R188933admin0_simplified.json", "--country=R188933")

        self.assertEqual(
            captured_output.getvalue(),
            "=== parsing R188933admin0_simplified.json\n ** adding Granica (R1000)\n ** removing unseen boundaries (R1000)\nOther unseen boundaries removed: 0\n ** updating paths for all of Granica\n",
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
