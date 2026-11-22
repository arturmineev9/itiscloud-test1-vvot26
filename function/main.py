"""Обработчик заданий из триггера Yandex Message Queue."""

import json
import os
import secrets
import shutil
import tempfile
import time
import urllib.request
import uuid
import zipfile


def uuid7():
    """UUIDv7: 48 бит времени в миллисекундах и случайные биты."""
    timestamp = time.time_ns() // 1_000_000
    value = (timestamp << 80) | (7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return uuid.UUID(int=value)


def download(url, target):
    with urllib.request.urlopen(url, timeout=60) as response:
        shutil.copyfileobj(response, target, length=1024 * 1024)


def process_event(event, storage, table, bucket):
    for message in event["messages"]:
        task = json.loads(message["details"]["message"]["body"])
        name, url = task["name"], task["url"]
        key = f"{uuid7()}.zip"
        with tempfile.TemporaryFile() as buffer:
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                with archive.open(name, "w", force_zip64=True) as target:
                    download(url, target)
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
