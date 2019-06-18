import gzip
import io

from temba.utils import json


class MockS3Client:
    """
    A mock of the boto S3 client
    """

    def __init__(self):
        self.objects = {}

    def put_jsonl(self, bucket, key, records):
        stream = io.BytesIO()
        gz = gzip.GzipFile(fileobj=stream, mode="wb")

        for record in records:
            gz.write(json.dumps(record).encode("utf-8"))
            gz.write(b"\n")
        gz.close()

        self.objects[(bucket, key)] = stream

    def get_object(self, Bucket, Key, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        return {"Bucket": Bucket, "Key": Key, "Body": stream}

    def delete_object(self, Bucket, Key, **kwargs):
        del self.objects[(Bucket, Key)]
        return {"DeleteMarker": False, "VersionId": "versionId", "RequestCharged": "requester"}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        matches = []
        for o in self.objects.keys():
            if o[1].startswith(Prefix):
                matches.append({"Key": o[1]})

        return dict(Contents=matches)
