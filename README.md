# Контрольная работа № 1: система архивирования

API принимает ссылку на файл и ставит задание в очередь. Облачная функция создаёт ZIP в Object Storage и записывает метаданные в YDB. Через API можно получить список архивов, сведения об архиве и скачать его.

## Состав проекта

- `openapi.yaml` — спецификация API Gateway.
- `function/` — Python-функция и зависимости.
- `terraform/` — инфраструктура Yandex Cloud.
- `tests/` — локальные тесты функции.
- `cleanup_bucket.py` — очистка бакета перед удалением ресурсов.

## Подготовка

Нужны Git, Terraform, Yandex Cloud CLI и Python 3. В CLI должен быть настроен профиль с доступом к облаку и каталогу и правами создавать ресурсы и назначать роли. Команды ниже выполняются в Bash (Linux или Ubuntu/WSL).

Клонируйте репозиторий и запустите тесты:

```bash
git clone https://github.com/arturmineev9/itiscloud-test1-vvot26.git
cd itiscloud-test1-vvot26
python3 -m unittest discover -s tests -v
cd terraform
```

## Развёртывание

Используйте пустой тестовый каталог. Имена ресурсов начинаются с `vvot26-`; имя бакета должно быть глобально уникальным и иметь этот префикс.

```bash
export YC_TOKEN="$(yc iam create-token)"
export TF_VAR_cloud_id="$(yc config get cloud-id)"
export TF_VAR_folder_id="$(yc config get folder-id)"
export TF_VAR_bucket_name="vvot26-archives-${TF_VAR_folder_id}"

terraform init
terraform validate
```

Первый запуск выполняется в два этапа: AWS-совместимому провайдеру для создания таблицы нужны ключ и адрес уже созданной базы YDB.

```bash
terraform plan \
  -target=yandex_iam_service_account_static_access_key.system \
  -target=yandex_resourcemanager_folder_iam_member.roles \
  -target=yandex_ydb_database_serverless.archives \
  -out=bootstrap.tfplan
terraform apply bootstrap.tfplan

terraform plan -out=deployment.tfplan
terraform apply deployment.tfplan
terraform output
```

Изучите планы перед применением. Не публикуйте файлы состояния и планов Terraform: они могут содержать статический секрет.

## Проверка API

Из каталога `terraform`:

```bash
API="$(terraform output -raw api_url)"
curl -i -H 'Content-Type: application/json' \
  -d '{"name":"example.html","url":"https://example.com/"}' "$API/tasks"
curl -s -w '\n' "$API/archives"
```

Успешная отправка возвращает `202`. После появления записи в списке скопируйте её полный ключ вместе с `.zip`:

```bash
KEY="ключ-архива.zip"
curl -i -w '\n' "$API/archives/$KEY"
curl -f -o archive.zip "$API/download/$KEY"
python3 -m zipfile -l archive.zip
```

Для некорректного задания возвращается `400`, для отсутствующей записи или файла — `404`. Готовый ZIP ограничен 2 500 000 байт.

## Удаление ресурсов

Сначала удалите ненужные объекты из созданного бакета. Для этого можно использовать скрипт из корня репозитория:

```bash
cd ..  # из каталога terraform
python3 -m venv .venv
.venv/bin/python -m pip install -r function/requirements.txt
.venv/bin/python cleanup_bucket.py
```

Команда без аргументов показывает объекты. После проверки списка команда ниже **необратимо удалит все перечисленные объекты**:

```bash
.venv/bin/python cleanup_bucket.py --delete
cd terraform
terraform destroy
```

Перед подтверждением `destroy` проверьте план; после завершения убедитесь, что ресурсы удалены. Используйте то же локальное состояние и те же значения переменных, что и при развёртывании.
