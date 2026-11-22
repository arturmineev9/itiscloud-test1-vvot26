"""List or delete objects in the Terraform-managed coursework bucket."""
import argparse
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / ".deps"))
import boto3

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--delete", action="store_true", help="Delete the listed objects")
args = parser.parse_args()
state = json.loads((root / "terraform/terraform.tfstate").read_text(encoding="utf-8"))
resources = {(r["type"], r["name"]): r["instances"][0]["attributes"] for r in state["resources"] if r.get("instances")}
key = resources[("yandex_iam_service_account_static_access_key", "system")]
bucket = resources[("yandex_storage_bucket", "archives")]["bucket"]
if not bucket.startswith("vvot26-"):
    raise SystemExit("Unexpected bucket name; refusing to continue")
client = boto3.client("s3", region_name="ru-central1", endpoint_url="https://storage.yandexcloud.net", aws_access_key_id=key["access_key"], aws_secret_access_key=key["secret_key"])
pages = client.get_paginator("list_objects_v2")
objects = [{"Key": item["Key"]} for page in pages.paginate(Bucket=bucket) for item in page.get("Contents", [])]
print(f"Bucket {bucket}: {len(objects)} objects")
for item in objects:
    print(item["Key"])
if args.delete:
    for index in range(0, len(objects), 1000):
        response = client.delete_objects(Bucket=bucket, Delete={"Objects": objects[index:index + 1000]})
        if response.get("Errors"):
            raise SystemExit(f"Deletion errors: {response['Errors']}")
    print("Objects deleted")
else:
    print("Preview only. Use --delete when ready to delete these objects.")
