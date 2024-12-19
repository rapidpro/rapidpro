import gzip
import hashlib
import io

from temba.archives.models import jsonlgz_rewrite
from temba.tests import TembaTest


class JSONLGZTest(TembaTest):
    def test_jsonlgz_rewrite(self):
        def rewrite(b: bytes, transform):
            in_file = io.BytesIO(b)
            out_file = io.BytesIO()
            md5, size = jsonlgz_rewrite(in_file, out_file, transform)
            return out_file.getvalue(), md5.hexdigest(), size

        data = b'{"id": 123, "name": "Jim"}\n{"id": 234, "name": "Bob"}\n{"id": 345, "name": "Ann"}\n'
        gzipped = gzip.compress(data)

        # rewrite that using a pass-through transform for each record
        data1, hash1, size1 = rewrite(gzipped, lambda r: r)

        self.assertEqual(data, gzip.decompress(data1))
        self.assertEqual(hashlib.md5(data1).hexdigest(), hash1)
        self.assertEqual(68, size1)

        # should get the exact same file and hash if we just repeat that
        data2, hash2, size2 = rewrite(gzipped, lambda r: r)

        self.assertEqual(data1, data2)
        self.assertEqual(hash1, hash2)
        self.assertEqual(68, size2)

        # rewrite with a transform that modifies each record
        def name_to_upper(record) -> dict:
            record["name"] = record["name"].upper()
            return record

        data3, hash3, size3 = rewrite(gzipped, name_to_upper)

        self.assertEqual(
            b'{"id": 123, "name": "JIM"}\n{"id": 234, "name": "BOB"}\n{"id": 345, "name": "ANN"}\n',
            gzip.decompress(data3),
        )
        self.assertEqual(hashlib.md5(data3).hexdigest(), hash3)
        self.assertEqual(68, size3)

        # rewrite with a transform that removes a record
        def remove_bob(record) -> dict:
            return None if record["id"] == 234 else record

        data4, hash4, size4 = rewrite(gzipped, remove_bob)

        self.assertEqual(b'{"id": 123, "name": "Jim"}\n{"id": 345, "name": "Ann"}\n', gzip.decompress(data4))
        self.assertEqual(hashlib.md5(data4).hexdigest(), hash4)
        self.assertEqual(58, size4)
