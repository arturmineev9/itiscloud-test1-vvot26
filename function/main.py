"""Обработчик заданий из триггера Yandex Message Queue."""

import json
import hashlib
import http.client
import ipaddress
import os
import shutil
import socket
import ssl
import tempfile
import urllib.parse
import uuid
import zipfile


def message_uuid(message):
    message_id = message.get("message_id")
    timestamp = message.get("attributes", {}).get("SentTimestamp")
    if message_id is None or timestamp is None:
        raise ValueError("Queue message lacks ID or timestamp")
    random_bits = int.from_bytes(hashlib.sha256(message_id.encode("utf-8")).digest()[:10], "big")
    value = (int(timestamp) << 80) | (7 << 76) | (((random_bits >> 62) & 0xfff) << 64)
    value |= (0b10 << 62) | (random_bits & ((1 << 62) - 1))
    return uuid.UUID(int=value)


MAX_ARCHIVE_BYTES = 2_500_000


def public_address(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or (mapped is not None and not mapped.is_global):
            raise ValueError("Source resolves to a non-public address")
    if not addresses:
        raise ValueError("Source has no address")
    return addresses[0][4][0]


def download(url, target):
    for _ in range(6):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Invalid source URL")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = public_address(parsed.hostname, port)
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=60)
        try:
            connection.sock = socket.create_connection((address, port), timeout=60)
            if parsed.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=parsed.hostname)
            path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request("GET", path, headers={"Host": parsed.netloc, "Connection": "close"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect without Location")
                url = urllib.parse.urljoin(url, location)
                continue
            if not 200 <= response.status < 300:
                raise OSError(f"Source returned HTTP {response.status}")
            shutil.copyfileobj(response, target, length=1024 * 1024)
            return
        finally:
            connection.close()
    raise ValueError("Too many redirects")


def process_event(event, storage, table, bucket):
    for message in event["messages"]:
        queue_message = message["details"]["message"]
        task = json.loads(queue_message["body"])
        name, url = task["name"], task["url"]
        if not isinstance(name, str) or not name.strip() or name in (".", "..") or any(c in name for c in ("/", "\\")) or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("Invalid file name")
        key = f"{message_uuid(queue_message)}.zip"
        with tempfile.TemporaryFile() as buffer:
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                with archive.open(name, "w", force_zip64=True) as target:
                    download(url, target)
            if buffer.tell() > MAX_ARCHIVE_BYTES:
                raise ValueError("Archive exceeds API Gateway response limit")
            buffer.seek(0)
            storage.put_object(Bucket=bucket, Key=key, Body=buffer, ContentType="application/zip")
        table.put_item(Item={"name": name, "url": url, "key": key})


def handler(event, context):
    import boto3

    session = boto3.session.Session(region_name="ru-central1")
    storage = session.client("s3", endpoint_url="https://storage.yandexcloud.net")
    dynamodb = session.resource("dynamodb", endpoint_url=os.environ["YDB_DOCUMENT_ENDPOINT"])
    process_event(event, storage, dynamodb.Table("archives"), os.environ["BUCKET_NAME"])
    return {"statusCode": 200}
