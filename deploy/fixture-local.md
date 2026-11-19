# Первичная связка локальной Moodle и PrairieLearn

Для каждого вида стенда создайте свой закрытый каталог вне репозитория
с правами `0700`, отдельное имя проекта Compose и приватный env-файл.
Подготовьте Docker Compose с поддержкой `!reset`, OpenSSL, `go-task`,
закреплённую Platform (`git submodule update --init`) и образы Moodle,
Gateway и Squid. Используйте чистый checkout готовой Native-поставки
и штатный реестр `pl-courses-v2` с одним курсом на указанном commit.
В каталоге курса должен быть один экземпляр Native. Для проверки Java
используйте опубликованные материалы выбранной обычной и экзаменационной
работы в разных проектах.

Пример входов обычного проекта в `/private/ordinary.env`.

```dotenv
COMPOSE_PROJECT_NAME=ordinary-fixture-local
FIXTURE_DIR=/private/ordinary
FIXTURE_NATIVE_DIR=/private/native/ordinary
FIXTURE_REGISTRY=/private/ordinary-courses.json
PL_HOST=pl.ordinary.test
PL_HTTPS_PORT=21646
MOODLE_HOST=moodle.ordinary.test
MOODLE_HTTPS_PORT=21645
```

Для `/private/exam.env` используйте другие значения перечисленных входов
и добавьте следующие. `GATEWAY_IMAGE` должен поддерживать `examAudit` и
проверенный заголовок от Nginx. Оба образа укажите по проверенному digest
или по локальному тегу с заранее проверенной ревизией. По умолчанию Moodle
собирается штатным Dockerfile; готовый образ можно указать как
`FIXTURE_MOODLE_IMAGE`. Выделите частную подсеть `/29`, свободную на хосте
и в Docker. Первый доступный адрес станет шлюзом ingress, второй получит
Nginx, третий Squid. Порт Squid публикуется только на `127.0.0.1`.

```dotenv
COMPOSE_PROJECT_NAME=exam-fixture-local
FIXTURE_DIR=/private/exam
FIXTURE_NATIVE_DIR=/private/native/exam
FIXTURE_REGISTRY=/private/exam-courses.json
PL_HOST=pl.exam.test
PL_HTTPS_PORT=21644
MOODLE_HOST=moodle.exam.test
MOODLE_HTTPS_PORT=21643
GATEWAY_IMAGE=local/gateway:exam-tested
EXAM_ID=exam-lab-pilot
EXAM_INGRESS_SUBNET=172.16.252.0/29
EXAM_PROXY_PUBLISH_PORT=21680
EXAM_PROXY_IMAGE=site-exam-squid:7
```

Из корня Deployment запустите обе fixture. Дополнительная командная
настройка сгенерированных файлов после запуска не нужна.

```sh
go-task fixture:up FIXTURE=ordinary FIXTURE_ENV=/private/ordinary.env
go-task fixture:up FIXTURE=exam FIXTURE_ENV=/private/exam.env
```

В начале команда проверяет, что путь привязан к своей fixture и Native
соответствует реестру. Compose запускает штатный `config-init`, Moodle
выдаёт регистрацию, а Platform выполняет `Bootstrap` и `Sync`. Настройки
Bridge и Gateway используют фактические Native ID/UUID и подписанный LTI
контекст тестовой Moodle. Экзаменационный запуск подставляет выбранную
activity и регистрацию в узкий ACL Squid, проверяет итоговый конфиг его
штатным бинарным файлом, затем публикует только Squid. Выданные ссылки на
Moodle activity и список работ появляются после проверки готовности.
Адрес шлюза Docker в учёте Gateway характеризует путь через локальный NAT.

Пароли синтетических учёток находятся в `FIXTURE_DIR/state/moodle-accounts.json`
в закрытом состоянии. При необходимости передайте нужную учётку через
административный канал. Обычный тестовый клиент проверяет `FIXTURE_DIR/state/tls/ca.crt`
и разрешает тестовые имена на `127.0.0.1`. Клиент экзамена передаёт запрос
только через Squid и проверяет `FIXTURE_DIR/squid-ca.pem` либо его копию
`squid-ca.cer` в DER. Установщик Windows получает только проверенный публичный
сертификат и его ожидаемый SHA-256; для DER используйте `sha256sum`.
Закрытый ключ Squid остаётся в серверной области. Squid сам проверяет Nginx по
публичному CA из `state/tls`.

Повторите ту же команду, сохранив project, приватный каталог, оба origin,
реестр и checkout. Docker-том `<project>_fixture_owner` закрепляет имя
проекта за каталогом и не монтируется в сервисы. Сохраняйте его вместе
с томами данных. Origin включает порт и участвует в идентичности
LTI-студента. Для другой работы, имени сервера или порта используйте новые
имя проекта и каталог. `fixture:status` показывает Compose-сервисы,
`fixture:down` останавливает их с сохранением state и именованных томов.
Обе команды принимают те же `FIXTURE` и `FIXTURE_ENV`. Проверяйте обычный LTI,
выбранную экзаменационную activity и фактическую сдачу до и после повтора.

## HTTPS контроллера

Для отдельного Windows-пилота создайте закрытый каталог контроллера вне
репозитория. Установите OpenSSL и Nginx. На этом стенде QEMU передаёт
`10.0.2.55:21650` гостевой Windows на `127.0.0.1:21650` хоста. IP `10.0.2.55`
записан в SAN сертификата. Свой CA используется только для контроллера.

```sh
install -d -m 700 /private/controller
bash scripts/exam-controller-tls.sh /private/controller
nginx -t -p /private/controller/ -c nginx.conf
nginx -p /private/controller/ -c nginx.conf
```

Сценарий создаёт сертификаты при первой подготовке, проверяет соответствие
ключей и выводит SHA-256 публичного `controller-ca.cer` при каждом повторе.
Передайте Windows этот публичный сертификат и ожидаемый отпечаток через
административный комплект. Контроллер exam-lab отдельно запускается на
`127.0.0.1:21651` и регистрирует секрет каждого ПК из закрытого состояния.
Nginx принимает только `POST /exam/v1/poll`; журналы доступа отключены.
Для остановки используйте `nginx -s quit -p /private/controller/ -c nginx.conf`.
