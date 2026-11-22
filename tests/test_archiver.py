import importlib.util
import io
import json
import pathlib
import unittest
import zipfile
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location("archiver", pathlib.Path(__file__).parents[1] / "function" / "main.py")
archiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archiver)


def with_metadata(event):
    for item in event["messages"]:
        message = item["details"]["message"]
        message.setdefault("message_id", "test-message")
        message.setdefault("attributes", {"SentTimestamp": "1700000000000"})
    return event


class ArchiverTests(unittest.TestCase):
    def test_blocks_private_resolved_address(self):
        with patch.object(archiver.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]):
            with self.assertRaises(ValueError):
                archiver.public_address("example.org", 80)

    def test_redirect_to_private_address_is_blocked(self):
        response = MagicMock(status=302)
        response.getheader.return_value = "http://169.254.169.254/"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(archiver, "public_address", side_effect=["93.184.215.14", ValueError("private")]), patch.object(archiver.http.client, "HTTPConnection", return_value=connection), patch.object(archiver.socket, "create_connection"):
            with self.assertRaises(ValueError):
                archiver.download("http://example.org/", io.BytesIO())

    def test_same_message_produces_same_key(self):
        message = {"message_id": "msg-123", "attributes": {"SentTimestamp": "1700000000000"}}
        first = archiver.message_uuid(message)
        self.assertEqual(first, archiver.message_uuid(message))
        self.assertEqual(first.version, 7)

    def test_rejects_unsafe_filename(self):
        event = {"messages": [{"details": {"message": {"body": json.dumps({"name": "../x", "url": "https://example.org/x"})}}}]}
        with self.assertRaises(ValueError):
            archiver.process_event(with_metadata(event), FakeStorage(), FakeTable(), "bucket")

    def test_rejects_archive_above_gateway_limit(self):
        event = {"messages": [{"details": {"message": {"body": '{"name":"x","url":"https://example.org/x"}'}}}]}
        storage = FakeStorage()
        with patch.object(archiver, "download", side_effect=lambda url, target: target.write(b"abc")), patch.object(archiver, "MAX_ARCHIVE_BYTES", 1):
            with self.assertRaises(ValueError):
                archiver.process_event(with_metadata(event), storage, FakeTable(), "bucket")
        self.assertEqual(storage.objects, {})

    def test_archive_contains_named_file_and_metadata(self):
        storage = FakeStorage()
        table = FakeTable()
        event = {"messages": [{"details": {"message": {"body": json.dumps({"name": "hello.txt", "url": "https://example.org/hello.txt"})}}}]}
        with patch.object(archiver, "download", side_effect=lambda url, target: target.write(b"hello")):
            archiver.process_event(with_metadata(event), storage, table, "bucket")
        self.assertEqual(len(table.items), 1)
        item = table.items[0]
        self.assertEqual((item["name"], item["url"]), ("hello.txt", "https://example.org/hello.txt"))
        self.assertRegex(item["key"], r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.zip$")
        with zipfile.ZipFile(io.BytesIO(storage.objects[item["key"]])) as archive:
            self.assertEqual(archive.read("hello.txt"), b"hello")

    def test_download_failure_propagates(self):
        event = {"messages": [{"details": {"message": {"body": '{"name":"x","url":"https://example.org/x"}'}}}]}
        table = FakeTable()
        with patch.object(archiver, "download", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                archiver.process_event(with_metadata(event), FakeStorage(), table, "bucket")
        self.assertEqual(table.items, [])

    def test_storage_failure_propagates_without_database_write(self):
        event = {"messages": [{"details": {"message": {"body": '{"name":"x","url":"https://example.org/x"}'}}}]}
        storage, table = FakeStorage(), FakeTable()
        with patch.object(archiver, "download", side_effect=lambda url, target: target.write(b"x")), patch.object(storage, "put_object", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError):
                archiver.process_event(with_metadata(event), storage, table, "bucket")
        self.assertEqual(table.items, [])

    def test_database_failure_propagates(self):
        event = {"messages": [{"details": {"message": {"body": '{"name":"x","url":"https://example.org/x"}'}}}]}
        table = FakeTable()
        with patch.object(archiver, "download", side_effect=lambda url, target: target.write(b"x")), patch.object(table, "put_item", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                archiver.process_event(with_metadata(event), FakeStorage(), table, "bucket")


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body.read()


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, *, Item):
        self.items.append(Item)


if __name__ == "__main__":
    unittest.main()
