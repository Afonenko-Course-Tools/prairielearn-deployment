# Необязательная передача клиентского IP в Gateway

Это дополнение связывает проверенный LTI UID/подписанное отображаемое имя с наблюдаемым IP. IP не является
аппаратным отпечатком или доказательством присутствия в конкретной аудитории:
NAT и общие прокси могут объединять несколько ПК. Разрешённая владельцем сеть
`10.0.0.0/8` относится к клиентам, а не к списку доверенных прокси.
Функция Gateway по умолчанию выключена; обычный LTI-путь не требует этого include.

## Готовая опциональная цепочка

1. Обычный standalone [exam-squid.conf](exam-squid.conf) сохраняет
   `forwarded_for delete`. Для включения IP transport сохраните полный
   согласованный конфиг Squid как `${EXAM_POLICY_DIR}/squid-base.conf`.
   Это базовый конфиг, а не wrapper ниже; он сохраняет прежние listener/source ACL,
   TLS verification и парные CONNECT/SNI/Host/destination ограничения.
2. Укажите абсолютный путь готового [exam-identity-squid.conf](exam-identity-squid.conf)
   в существующем `EXAM_SQUID_CONFIG`. Штатный include читает base через уже
   существующий policy bind; новый Compose profile или генератор не нужен.
   Первый `follow_x_forwarded_for deny all` игнорирует подмену клиента даже при
   последующем allow в base. Последний `forwarded_for truncate` заменяет прежний
   scalar delete и передаёт один фактический TCP source IP. Он применяется
   к пересылаемым разрешённым запросам, включая Moodle; это явное opt-in изменение.
3. Подключите [exam-client-ip.nginx.conf](exam-client-ip.nginx.conf) внутри
   `http {}` существующего Nginx. Замените все три вхождения `192.0.2.10`
   одним **точным фактическим адресом Squid peer** после NAT. Не доверяйте всему
   `10.0.0.0/8`, bridge subnet или всем loopback-клиентам. Доверенный транспорт
   предполагает, что посторонний процесс не может обращаться с адреса этого peer.
4. В location, который отправляет LTI-запросы Gateway, перезапишите заголовки:

   ```nginx
   include /run/private/exam-proxy-token.conf;
   proxy_set_header X-Exam-Client-IP $exam_verified_client_ip;
   ```

   Закрытый include содержит ровно одну строку
   `proxy_set_header X-Exam-Proxy-Token "<случайный секрет>";`.
   Его bytes совпадают с отдельным `proxyTokenFile` Gateway. Секрет и include хранятся
   вне Git, с доступом только оператора/Nginx; права Gateway tokenFile — `0600`.
   Нельзя подставлять браузерные X-Exam-* или X-Forwarded-For напрямую.
   Proxy destination должен оставаться внутренним Gateway, без публичного
   прямого доступа, позволяющего обойти этот producer.

5. Сначала включите только наблюдение Gateway. Явный launch-only CIDR запрет
   выбирается отдельно; он не ограничивает уже выданные PL cookies или websocket
   сессии и не заменяет Windows firewall.

При прямом запросе Nginx игнорирует подменённый XFF и использует TCP source.
На внутреннем hop доверяется только точному Squid peer; один адрес обеспечивает
native truncate. Realip умеет нормализовать IP:port/цепочку от доверенного peer;
этот include не является дополнительным валидатором скомпрометированного Squid.
Пропущенный/неразбираемый заголовок или отсутствие изменения доверенного peer
дают пустой X-Exam-Client-IP (в nginx пустой proxy header не отправляется).
Gateway трактует это как unknown. Технический адрес прокси не подменяет
неизвестный адрес ПК; потребитель получает bare IP из $remote_addr.

## Проверка и пределы

Перед запуском оператор выполняет штатный `squid-openssl -k parse` и `nginx -t`
с окончательными mounted inputs, затем проверяет клиент через настоящий TLS bump.
Нужно отдельно убедиться, что до Nginx действительно доходит выбранный peer Squid
и не требуется доверять общему NAT-адресу с посторонними клиентами.
Изменение include требует явного штатного reload/recreate собственного сервиса;
обычный старт PL это дополнение не включает. Живую учебную установку этот пример
не меняет.

Требуются Squid OpenSSL 7 (`truncate` документирован для v7) и Nginx с
`--with-http_realip_module`; `$realip_remote_addr` доступен с1.9.7. По текущему
Squid7.7 parser отсутствие файла include является ошибкой, поэтому вместо
необязательного wildcard используется явный opt-in wrapper.

Первичные источники:
[Squid forwarded_for](https://www.squid-cache.org/Doc/config/forwarded_for/),
[Squid follow_x_forwarded_for](https://www.squid-cache.org/Doc/config/follow_x_forwarded_for/),
[Squid7.7 parser](https://github.com/squid-cache/squid/blob/SQUID_7_7/src/cache_cf.cc#L288-L303),
[Nginx realip](https://nginx.org/en/docs/http/ngx_http_realip_module.html).

## Локальная проверка 17 ноября 2026 года

Проверен существующий Squid7.7 image
`sha256:a448d50a2d8e9d775c8a289c26806ae66438455829f85505adc81593890dde55`
и системный Nginx1.30.6. В изолированной цепочке клиент `127.0.0.3` → Squid
с исходящим peer `127.0.0.2` → Nginx TLS → синтетический HTTP upstream получены:

- Обычный base с delete не передал XFF: адрес клиента остался unknown.
- Wrapper передал ровно один фактический IP `127.0.0.3`, несмотря на подменённые
  XFF со списком/портом и оба браузерных X-Exam-* headers. Token заменён закрытым
  include; base намеренно содержал follow allow all, который не обошёл первый deny.
- Прямой клиент и недоверенный peer не смогли подставить IP из разрешённой сети.
  От доверенного peer без XFF либо с неразбираемым значением получен unknown.
- Несогласованная пара PL CONNECT / Moodle inner Host получила403; следующий
  разрешённый запрос снова прошёл. Запрос foreign.invalid первоначально получил503
  из-за TLS-пути до HTTP ACL; этот ответ не принят за проверку ACL.
- Client TLS и Squid upstream verification включены; nginx -t и Squid parse
  прошли для обычного base и opt-in wrapper. После опыта свои процессы/контейнеры
  остановлены; серверная конфигурация и глобальное доверие не менялись.

Это проверка producer IP transport. Синтетический upstream не подтверждает
реальный Gateway/LTI, вход студента, Windows или развёртывание в разрешённой
сети10.0.0.0/8. Для изолированного опыта использовались loopback и отдельные
тестовые TLS-порты вместо production443; публичные source allowlist не менялись.
