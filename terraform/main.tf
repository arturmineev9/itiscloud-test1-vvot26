terraform {
  required_providers {
    yandex  = { source = "yandex-cloud/yandex", version = "~> 0.188" }
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

variable "cloud_id" { type = string }
variable "folder_id" { type = string }
variable "bucket_name" {
  type = string
  validation {
    condition     = can(regex("^vvot26-[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.bucket_name)) && length(var.bucket_name) <= 63
    error_message = "Имя бакета должно начинаться с vvot26-, оканчиваться буквой или цифрой и быть не длиннее 63 символов."
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
}

resource "yandex_iam_service_account" "system" {
  name = "vvot26-archive-service"
}

resource "yandex_iam_service_account" "trigger" {
  name = "vvot26-trigger-service"
}

resource "yandex_resourcemanager_folder_iam_member" "roles" {
  for_each  = toset(["ymq.admin", "storage.editor", "ydb.editor"])
  folder_id = var.folder_id
  role      = each.value
  member    = "serviceAccount:${yandex_iam_service_account.system.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "trigger_roles" {
  for_each  = toset(["editor", "functions.functionInvoker"])
  folder_id = var.folder_id
  role      = each.value
  member    = "serviceAccount:${yandex_iam_service_account.trigger.id}"
}

resource "yandex_iam_service_account_static_access_key" "system" {
  service_account_id = yandex_iam_service_account.system.id
}

resource "yandex_storage_bucket" "archives" {
  bucket     = var.bucket_name
  folder_id  = var.folder_id
  max_size   = 10737418240
  access_key = yandex_iam_service_account_static_access_key.system.access_key
  secret_key = yandex_iam_service_account_static_access_key.system.secret_key
  depends_on = [yandex_resourcemanager_folder_iam_member.roles]
}

resource "yandex_ydb_database_serverless" "archives" {
  name = "vvot26-archives"
  serverless_database {
    enable_throttling_rcu_limit = true
    throttling_rcu_limit        = 10
    storage_size_limit          = 1
  }
}

provider "aws" {
  region                      = "ru-central1"
  access_key                  = yandex_iam_service_account_static_access_key.system.access_key
  secret_key                  = yandex_iam_service_account_static_access_key.system.secret_key
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
  endpoints {
    dynamodb = yandex_ydb_database_serverless.archives.document_api_endpoint
  }
}

resource "aws_dynamodb_table" "archives" {
  name         = "archives"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"
  attribute {
    name = "key"
    type = "S"
  }
  depends_on = [yandex_resourcemanager_folder_iam_member.roles, yandex_iam_service_account_static_access_key.system, yandex_ydb_database_serverless.archives]
}

resource "yandex_message_queue" "failed" {
  name                      = "vvot26-failed-tasks"
  message_retention_seconds = 1209600
  access_key                = yandex_iam_service_account_static_access_key.system.access_key
  secret_key                = yandex_iam_service_account_static_access_key.system.secret_key
  depends_on                = [yandex_resourcemanager_folder_iam_member.roles]
}

resource "yandex_message_queue" "tasks" {
  name                       = "vvot26-tasks"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 86400
  access_key                 = yandex_iam_service_account_static_access_key.system.access_key
  secret_key                 = yandex_iam_service_account_static_access_key.system.secret_key
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.failed.arn
    maxReceiveCount     = 1
  })
}

data "archive_file" "function" {
  type        = "zip"
  source_dir  = "${path.module}/../function"
  output_path = "${path.module}/function.zip"
  excludes    = ["__pycache__"]
}

resource "yandex_function" "archiver" {
  name               = "vvot26-archiver"
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = 256
  execution_timeout  = 600
  service_account_id = yandex_iam_service_account.system.id
  user_hash          = data.archive_file.function.output_base64sha256
  content { zip_filename = data.archive_file.function.output_path }
  environment = {
    BUCKET_NAME           = yandex_storage_bucket.archives.bucket
    YDB_DOCUMENT_ENDPOINT = yandex_ydb_database_serverless.archives.document_api_endpoint
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.system.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.system.secret_key
  }
  depends_on = [aws_dynamodb_table.archives]
}

resource "yandex_function_trigger" "queue" {
  name = "vvot26-task-trigger"
  function {
    id                 = yandex_function.archiver.id
    service_account_id = yandex_iam_service_account.trigger.id
  }
  message_queue {
    queue_id           = yandex_message_queue.tasks.arn
    service_account_id = yandex_iam_service_account.trigger.id
    batch_cutoff       = 0
    batch_size         = 1
  }
  depends_on = [yandex_resourcemanager_folder_iam_member.trigger_roles]
}

resource "yandex_api_gateway" "api" {
  name = "vvot26-archive-api"
  spec = templatefile("${path.module}/../openapi.yaml", {
    service_account_id = yandex_iam_service_account.system.id
    queue_url          = yandex_message_queue.tasks.id
    folder_id          = var.folder_id
    database_path      = yandex_ydb_database_serverless.archives.database_path
    bucket_name        = yandex_storage_bucket.archives.bucket
  })
  depends_on = [aws_dynamodb_table.archives, yandex_resourcemanager_folder_iam_member.roles]
}

output "api_url" { value = "https://${yandex_api_gateway.api.domain}" }
output "failed_queue_url" { value = yandex_message_queue.failed.id }
