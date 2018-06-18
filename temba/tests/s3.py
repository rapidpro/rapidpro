import gzip
import io
import json


class MocksS3Client:
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
