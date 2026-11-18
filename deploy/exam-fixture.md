# Отдельная экзаменационная Moodle fixture

[Дополнение Compose](exam-fixture.yml) предназначено для локального испытания.
Оно оставляет один опубликованный порт `127.0.0.1:20580` у Squid, снимает
публикацию портов экзаменационного Nginx и соединяет их отдельной сетью
`172.16.251.0/29`. У Nginx адрес `172.16.251.2`, у Squid `172.16.251.3`.
Обычные команды Deployment используют свои прежние файлы Compose и параметры.
Проверены Docker Compose `5.7.1` и Docker Engine `29.9.3`. Дополнение использует
штатный тег `!reset` для публикаций Nginx и режима сети Squid.

## Подготовка

Используйте отдельный закрытый каталог стенда и отдельный env-файл. Задайте
в нём одно и то же новое имя для `COMPOSE_PROJECT_NAME` и `EXAM_PROJECT_NAME`.
Задайте собственные абсолютные `STATE_DIR`, `HOST_JOBS_DIR`, имена Moodle/PL
и два свободных порта. Для прогона использовались `moodle.exam.test:20543`
и `pl.exam.test:20544`. До переключения у тестового Nginx должен быть
`BIND_IP=127.0.0.1`. Проверьте, что диапазон `172.16.251.0/29` свободен на
хосте и не пересекается с подсетями Docker. Выделите свои named volumes,
тестовую Moodle registration и Native-каталог одной работы.

Поднимите обычную fixture с этим env-файлом. Примените штатный
[auth override](../config/README.md) к PL, установив `hasShib=true` с прежними
ключами своей fixture. Дождитесь завершения `bootstrap-admin` с кодом `0`.
Загрузите отдельно подготовленный Native-курс штатными средствами Platform.
Постройте настройки Bridge и Gateway из полученных Native ID/UUID и регистрации
этой Moodle. Готовность базовой Moodle и PL проверяется до переключения сети.

Подготовьте Squid по [инструкции отдельного прокси](exam-README.md), включая
собственный issuer, постоянные каталоги и штатную инициализацию leaf DB.
Используйте закреплённые upstream `hosts` и `dst` на `172.16.251.2` для обоих
имён и точных HTTPS-портов. В CA bundle добавьте CA этого Nginx. Привяжите Squid
к `172.16.251.3:20580`. Для проверенного локального Docker входа разрешён
источник `172.16.251.1/32`, который следует подтвердить в журнале. Это адрес
Docker после NAT. Он подходит для доступа к локальной fixture и не
идентифицирует Windows-ПК. Для передачи IP используйте `follow_x_forwarded_for deny all` перед
базовым конфигом и `forwarded_for truncate` после него, как в
[договоре передачи IP](exam-client-ip.md#готовая-опциональная-цепочка).
Для Moodle используйте [ACL входа и выбранного LTI](../docs/exam-moodle-acl.md),
сверив OIDC-маршрут с регистрацией своей fixture.

## Nginx и переключение

В закрытом `EXAM_NGINX_CONF_DIR` подготовьте `nginx.conf` копированием
`nginx-auth.conf`, созданного в `STATE_DIR`. Добавьте в `http {}` строку
`include /etc/nginx/exam/ingress.conf;`. В каждый `server {}` добавьте проверку
`if ($exam_ingress_allowed = 0) { return 403; }` перед маршрутами. В обычный
`location /gateway/` добавьте внутренний token include и подтверждённый IP
по [договору передачи IP](exam-client-ip.md).

```nginx
include /etc/nginx/private/exam-proxy-token.conf;
proxy_set_header X-Exam-Client-IP $exam_verified_client_ip;
```

В `GATEWAY_CONFIG_DIR/exam-proxy-token.conf` хранится операторский
`proxy_set_header X-Exam-Proxy-Token` с тем же секретом, что и у Gateway.
Сохраните штатную очистку `Forwarded` и замену `X-Forwarded-For` в Nginx.
Для `ingress.conf` возьмите [пример доверия к одному Squid](exam-client-ip.nginx.conf),
замените `192.0.2.10` на `172.16.251.3` и добавьте следующий доступ по TCP peer,
включая единственную самопроверку Nginx по loopback.

```nginx
map $realip_remote_addr $exam_squid_peer {
    default 0;
    172.16.251.3 1;
}

map "$exam_squid_peer|$realip_remote_addr|$uri" $exam_ingress_allowed {
    default 0;
    ~^1\| 1;
    "0|127.0.0.1|/healthz" 1;
}
```

В следующих командах укажите свой файл подключений Native-курса для PL
вместо `native.override.yml`. Перед переключением дождитесь готовности PL,
Bridge, Gateway и Moodle этой fixture. Используйте те же входы для `ps`,
`stop` и `down`, чтобы сохранить данные и ключи прогона.

```bash
exam_compose=(docker compose --env-file /private/exam/fixture.env --profile exam
  -f compose.yml -f /private/exam/native.override.yml -f compose.auth.yml
  -f deploy/exam-fixture.yml)
"${exam_compose[@]}" config --quiet
"${exam_compose[@]}" up --no-deps -d --wait proxy exam-proxy
"${exam_compose[@]}" ps
"${exam_compose[@]}" exec -T proxy nginx -t -c /etc/nginx/exam/nginx.conf
```

## Проверка границ

На Deployment `fc900c7`, Community `v2.7.0`, Moodle `5.3` и Squid `7.7`
проверены разные сети, каталоги курса, базы PL и Moodle, постоянные тома и
проекты `exam-fixture-20261118` и `ordinary-fixture-20261118`. Через Squid
с проверкой CA получены `200` для входа Moodle и `/healthz` PL, `403` для
неразрешённого `/course/index.php`. Squid увидел разрешённый источник
`172.16.251.1`. Источник `172.16.251.2` получил `403` без обращения к Moodle.
Прямые прежние порты исчезли, запрос к Nginx по его адресу в bridge получил
`403`. Из контейнера обычного контроля прямое соединение с private PL backend
экзаменационного проекта завершилось по тайм-ауту. Обычный контроль сохранил
свои loopback-порты и вход через реальный LTI.

Перед повтором проверьте перечисленные границы с тем же курсом и доверенными
сертификатами. Отдельно проверьте подписанный LTI, уже открытую экзаменационную
сессию с другой сети и сдачу. Перенос на сеть аудитории и доверенная привязка
адреса к ПК требуют собственной сетевой проверки.
