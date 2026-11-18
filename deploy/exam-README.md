# Необязательная поставка экзаменационного Squid

Это экспериментальный standalone Compose с profile `exam`. Обычные `compose.yml`,
steady runtime, preview и Task-команды его не подключают. Контроллера экзамена здесь
нет. Profile — удобство выбора сервиса, а не запрет запуска: явное имя сервиса
также активирует его в Compose.

Для необязательного сопоставления LTI-студента и сетевого источника есть отдельная
[инструкция доверенной передачи IP](exam-client-ip.md). Она использует существующие
Squid/Nginx и не включает новый режим автоматически; базовый proxy по-прежнему
удаляет X-Forwarded-For.

Готовый механизм — официальный Ubuntu 26.04 `squid-openssl` 7, с SSL-Bump и
штатным `security_file_certgen`. Собственного MITM addon, генератора policy,
launcher и управления Windows нет. Squid 6.14 не подходит для этого договора:
его OpenSSL-путь не учитывал IP SAN upstream, а fallback leaf кодировал IP как
DNS SAN. Исправления проверены в release 7.2 и 7.7; образ допускает `>=7.2,<8`.
В Ubuntu 26.04 нужен именно `/usr/sbin/squid-openssl`, обычный `squid` — другая
сборка. Native UID/GID проверяется при build и равен `13:13`.

В отдельной Linux canary на 7.7 подтверждены PL/Moodle с client TLS verify=0
и включённой upstream verification. Отдельный контроль с явным GlobalSign-only
bundle без PL CA сохранил Moodle 303 и дал PL собственный Squid 503
`ERR_SECURE_CONNECT_FAIL`; client TLS verify=0 в обоих случаях. PL access log
показал `HIER_NONE`. Это принятый negative отсутствующего upstream CA,
не проверка всех вариантов TLS-отказа и не Windows-приёмка.
Подтверждён собственный отказ Squid для постороннего Host, включая второй запрос
в том же TLS-соединении, и финальные разрешённые запросы после negatives.
Duplicate Host с разрешённым первым и посторонним вторым значением получил 303,
обратный порядок — 403. Exact release source объясняет первый случай: без
redirector/peer overrides Squid заменяет каждый Host на проверенную URI authority.
Могут остаться два одинаковых разрешённых Host; постороннее значение не копируется.
Это source-объяснение, не native capture upstream и не обещание отказа всех
duplicates или синтаксического мусора. Изменение redirector/cache_peer/header rewrite
требует повторной оценки этого договора.

Публичный standalone Compose отдельно проверен на Linux с образом 7.7, новым
issuer и отдельным state: native helper/parse без сети, `up`/`ps`/`down`, actual
UID/caps/binds и loopback listener. Независимые HEAD подтвердили PL 302, Moodle 303,
посторонний shared-IP Host `bsu.by` fresh и вторым запросом в том же TLS-соединении
получил собственный `TCP_DENIED/403` с `HIER_NONE`; финальный Moodle снова дал 303.
Client TLS verify=0; после `down` контейнер и listener отсутствуют, CA и leaf DB
сохранены. Отдельный foreign `.invalid` получил собственный 503
`ERR_SECURE_CONNECT_FAIL`: [исходники release](https://github.com/squid-cache/squid/blob/SQUID_7_7/src/client_side.cc#L1361-L1399) допускают certificate check до `http_access`,
но точная native причина не захвачена, поэтому этот случай не считается проверкой ACL.
Это локальная проверка поставки. Windows firewall, браузер/JVM, полный учебный
маршрут, сеть аудитории и серверное размещение ещё требуют native-пилота.

## Входы оператора

Скопируйте [exam-env.example](exam-env.example) в отдельный env-file вне Git.
Compose требует все пути существующими и не создаёт bind source автоматически.
Используйте отдельные project и state, без хранилищ PL/Moodle и Docker socket.
Все входы находятся на том же Linux Docker host.

| Вход                | Назначение                                                 |
| ------------------- | ---------------------------------------------------------- |
| `EXAM_PROJECT_NAME` | Отдельный Compose project                                  |
| `EXAM_PROXY_IMAGE`  | Точный проверенный image/digest или локальный image ID     |
| `EXAM_SQUID_CONFIG` | Полный доверенный operator-owned Squid config              |
| `EXAM_POLICY_DIR`   | Согласованные native `destinations.conf` и `hosts`         |
| `EXAM_TLS_DIR`      | Закрытые `issuer.crt`, `issuer.key`, `upstream-ca.pem`     |
| `EXAM_STATE_DIR`    | Единственный writable bind; закрытые `spool`, `log`, `run` |

[exam-squid.conf](exam-squid.conf) — безопасный local-only пример, listener
`127.0.0.1:18080` и source ACL только loopback. `network_mode: host` поддерживает
эту поставку на Linux; он не является сетевой изоляцией. Нет `ports`, `-p`, NAT
или изменения host firewall. Внешние Windows-клиенты к loopback не подключатся.
Для размещения на сервере оператор явно задаёт listener на нужном интерфейсе и
source ACL аудитории в своей копии config, согласует host firewall, endpoint и
доверие клиентов, затем повторяет native-пилот. Не заменяйте listener на все
интерфейсы без этого договора. Учебные source ACL не берутся из Git по умолчанию.

Policy directory — один согласованный вход поставки, а не второй независимый
список из среды Windows или `.env`. [destinations.conf](exam-policy/destinations.conf)
содержит парные проверки CONNECT, inner Host, client-requested TLS name и dst
каждого приложения. Повторы имени в разных native ACL намеренны. Не объединяйте
Host/SNI одного приложения с IP другого. [hosts](exam-policy/hosts) выполняет
другую роль: штатный static address pin, ровно один IP на разрешённое DNS имя.
Для PL с literal IP отдельная hosts-строка не нужна. Эти два native файла
обновляются и рецензируются вместе. Примеры используют TEST-NET и `.invalid`,
не действующие назначения. Перед запуском оператор заменяет их согласованными
адресами, экранирует точки в regex и проверяет все парные ACL.

Не удаляйте `hosts_file` в расчёте на один `dst`: при обычном DNS этот ACL
принимает совпадение любого cached IP и не закрепляет выбранный TCP endpoint.
В Squid 7 static hosts entry с одним адресом блокируется от истечения и DNS
подмены. Смена IP требует явного обновления policy и пересоздания контейнера.
Отсутствие внешних DNS-запросов здесь не доказано: host resolver и системная сеть
проверяются отдельно. Полный Squid config и native include — доверенный код
оператора, способный менять политику, а не sandbox-данные.

## TLS и закрытый state

Сервис получает отдельный issuer, предназначенный для экзаменационного proxy;
не используйте private key действующего PL/Moodle. `issuer.crt` должен позволять
подпись leaf, а закрытый ключ `issuer.key` — соответствовать ему. Issuer creation,
хранение, срок действия, обновление, отзыв и распространение доверия клиентам
остаются явным внешним договором оператора. Эта поставка не выпускает CA,
не устанавливает доверие и не обновляет сертификаты автоматически.

`upstream-ca.pem` содержит только согласованные публичные upstream CA и нужные
intermediate. При неполной цепочке сервера включите официальный intermediate в
этот private bundle; не отключайте проверку. Native config использует
`default-ca=off`, `sslproxy_cert_error deny all`, без `DONT_VERIFY_*` и splice.
Клиент проверяет имя/IP leaf и отдельный публичный issuer явным `--cacert` либо
через отдельно согласованное доверие своего режима. Host и Windows/JDK trust
этим Compose не меняются.

TLS directory и state принадлежат UID/GID `13:13`, каталоги `0700`, ключи и private
файлы `0600`; CA public copy клиента остаётся отдельно под его владельцем.
Создайте `state/spool`, `state/log`, `state/run` заранее штатным `install -d`
с этими правами. Config/policy должны читаться UID 13; они не содержат ключей.
State включает generated leaf private keys. HTTP content не кэшируется; access
log исключает URL/query, headers, Authorization, Cookie и body. Cache log и
private state не публикуют. Debug, который выводит key/session material, не
включается. Retention и удаление только собственного state задаёт оператор.

## Сборка и явный запуск

Команды выполняются из корня Deployment. Build context содержит только Dockerfile,
без policy, state и ключей. Официальный apt сохраняет security updates; после
сборки проверьте фактические версию, OpenSSL/ssl-crtd capabilities и UID. Запишите
точный проверенный image в `EXAM_PROXY_IMAGE`, сохранив предыдущий для возврата.

```sh
docker build --network=host -t site-exam-squid:7 deploy/exam-proxy
docker run --rm --network=none --entrypoint /usr/sbin/squid-openssl site-exam-squid:7 -v
docker run --rm --network=none --entrypoint dpkg-query site-exam-squid:7 -W squid-openssl
docker image inspect site-exam-squid:7 --format '{{.Id}}'
```

Во всех операциях используйте один env-file, project и standalone `-f`,
не добавляйте `compose.yml`. Для примера project равен `exam-canary` и должен
совпадать с `EXAM_PROJECT_NAME`. Read-only config не проверяет доступность входов,
права, TLS или свободный порт:

```sh
docker compose --env-file /srv/exam-proxy/exam.env -p exam-canary -f deploy/exam-compose.yml --profile exam config
```

Только для первоначально пустого собственного spool выполните штатный helper.
Не очищайте и не пересоздавайте существующую DB при повторном старте:

Для helper и parse ниже используется обычный `docker run --network=none`:
`docker compose run` не имеет такого flag. Пути примера должны совпадать с env-file;
`EXAM_PROXY_IMAGE` должен указывать на этот же проверенный image. State остаётся
единственным writable bind. Первый helper намеренно откажет для существующей DB.

```sh
EXAM_CHECK_IMAGE="$(docker image inspect site-exam-squid:7 --format '{{.Id}}')"
docker run --rm --network=none --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --mount type=bind,src=/srv/exam-proxy/squid.conf,dst=/etc/squid/squid.conf,readonly \
  --mount type=bind,src=/srv/exam-proxy/policy,dst=/etc/squid/exam-policy,readonly \
  --mount type=bind,src=/srv/exam-proxy/private-tls,dst=/run/exam-tls,readonly \
  --mount type=bind,src=/srv/exam-proxy/state,dst=/var/lib/exam-squid \
  --entrypoint /usr/lib/squid/security_file_certgen "$EXAM_CHECK_IMAGE" \
  -c -s /var/lib/exam-squid/spool/ssl_db -M 4MB

docker run --rm --network=none --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --mount type=bind,src=/srv/exam-proxy/squid.conf,dst=/etc/squid/squid.conf,readonly \
  --mount type=bind,src=/srv/exam-proxy/policy,dst=/etc/squid/exam-policy,readonly \
  --mount type=bind,src=/srv/exam-proxy/private-tls,dst=/run/exam-tls,readonly \
  --mount type=bind,src=/srv/exam-proxy/state,dst=/var/lib/exam-squid \
  --entrypoint /usr/sbin/squid-openssl "$EXAM_CHECK_IMAGE" \
  -k parse -f /etc/squid/squid.conf
```

Это одноразовые native-проверки без listener. При ошибке исправляют причину;
verification и Host ACL не ослабляют. После успешного parse и проверки свободного
listener:

```sh
docker compose --env-file /srv/exam-proxy/exam.env -p exam-canary -f deploy/exam-compose.yml --profile exam up -d
docker compose --env-file /srv/exam-proxy/exam.env -p exam-canary -f deploy/exam-compose.yml --profile exam ps
docker compose --env-file /srv/exam-proxy/exam.env -p exam-canary -f deploy/exam-compose.yml --profile exam down
```

Остановка удаляет только контейнер своего project и сохраняет bind state. Старт
процесса не доказывает применение network policy. Перед допуском независимо
проверьте TLS positives обоих приложений, actual peer IP, foreign Host fresh и
вторым запросом в том же соединении, разрешённый repeat, wrong/missing SNI,
ambiguous Host/absolute URI, nested CONNECT, non443, Upgrade/h2 и bad upstream CA.
Отказ должен исходить от Squid и предотвращать запрещённый HTTP upstream request;
SSL-Bump может заранее установить TLS к разрешённому endpoint.

## Первичные источники

- [Ubuntu package и версия](https://packages.ubuntu.com/resolute/squid-openssl)
- [OpenSSL binary/helper](https://packages.ubuntu.com/resolute/amd64/squid-openssl/filelist)
- [Release upstream IP verification](https://raw.githubusercontent.com/squid-cache/squid/SQUID_7_7/src/ssl/support.cc)
- [Release IP SAN и mimic](https://raw.githubusercontent.com/squid-cache/squid/SQUID_7_7/src/ssl/gadgets.cc)
- [Native Host forwarding](https://raw.githubusercontent.com/squid-cache/squid/SQUID_7_7/src/http.cc)
- [Static hosts cache](https://raw.githubusercontent.com/squid-cache/squid/SQUID_7_7/src/ipcache.cc)
- [SSL-Bump](https://www.squid-cache.org/Doc/config/ssl_bump/), [ACL](https://www.squid-cache.org/Doc/config/acl/), [Host check](https://www.squid-cache.org/Doc/config/host_verify_strict/)
- [Upstream TLS](https://www.squid-cache.org/Doc/config/tls_outgoing_options/), [certificate errors](https://www.squid-cache.org/Doc/config/sslproxy_cert_error/), [leaf DB](https://www.squid-cache.org/Doc/config/sslcrtd_program/)
