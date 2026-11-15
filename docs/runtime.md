# Работа с готовой восстановленной установкой

`compose.runtime.yml` — самостоятельный Compose entry для PL, Bridge, Gateway
и обычного HTTPS Nginx на 443. Он использует готовые native конфигурации, прежнюю
TLS identity и заранее восстановленные данные. Локальный fixture `compose.yml`
по-прежнему создаёт тестовую Moodle, ключи и первоначального administrator;
dev-preview сохраняет свои команды и state.

Runtime наследует существующие сервисы через штатный `extends`, заменяя только
входы конфигурации, bind mounts, зависимости и ingress. Нужен Docker Compose
не ниже 2.24.4 из-за стандартных `!reset`/`!override`, Linux/amd64 и submodule
`deps/platform` по принятому gitlink. Новый launcher, установщик пакетов или
собственная матрица версий не используются.

## Входы оператора

Скопируйте [.env.runtime.example](../.env.runtime.example) вне Git и заполните
его по сохранённому описанию исходной установки. Не используйте fixture `.env`
вместо этого файла. Он содержит только настройки и пути, секреты остаются
в прежних отдельных файлах.

| Переменная             | Назначение                                                                                                      |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- |
| `COMPOSE_PROJECT_NAME` | Явное имя выбранного проекта; для репетиции отдельное от действующего                                           |
| `DEPLOY_ROOT`          | Абсолютный существующий корень восстановленных `postgres`, `files`, `s3rver`, `courses`, `jobs`, `gateway-data` |
| `PL_IMAGE`             | Точный сохранённый Community image с digest для PL и Bridge                                                     |
| `GATEWAY_IMAGE`        | Точный сохранённый Gateway image с digest                                                                       |
| `NGINX_IMAGE`          | Точный сохранённый Nginx image с digest                                                                         |
| `PL_CONFIG_FILE`       | Готовый native PL config с прежними ключами, origins и явным `workersCount`                                     |
| `BRIDGE_CONFIG_DIR`    | Готовые `config.json`, реальные bindings и указанный в нём tokenFile                                            |
| `GATEWAY_CONFIG_DIR`   | Готовый `runtime.json`, LTI registration и его secrets/CA files                                                 |
| `NGINX_CONFIG`         | Готовый Nginx config с HTTPS listener 443 и прежним auth/handoff policy                                         |
| `TLS_DIR`              | Прежние `server.crt` и `server.key`, доступные внутри Nginx как `/tls/server.crt` и `/tls/server.key`           |
| `PROXY_PRIVATE`        | Прежний `handoff-auth.conf`, доступный как `/etc/nginx/private/handoff-auth.conf`                               |
| `PL_CA_FILE`           | Публичный CA bundle для проверки прежнего HTTPS leaf; внутри Nginx `/run/pl-tls/ca.crt`                         |
| `PL_TLS_NAME`          | Прежнее DNS имя или IPv4 из TLS identity, без схемы и порта                                                     |
| `BIND_IP`              | Адрес публикации 443 на новой машине; по умолчанию `127.0.0.1`                                                  |

`NGINX_CONFIG` монтируется read-only в `/etc/nginx/nginx.conf`. Он должен
обслуживать `/healthz`, обращаться к `pl:3000` и `gateway:18080`, закрывать
публичные `/internal/` и `/pl/shibcallback` и сохранять существующий проверенный
handoff. Этот entry не генерирует Nginx config, сертификат или auth-настройки.
Содержимое fixture `config/nginx.auth.conf` с placeholder и Moodle server
не является готовым runtime config.

Все host bind paths должны существовать до запуска: `create_host_path: false`
запрещает Compose создавать отсутствующие каталоги вместо них. PL получает
`HOST_JOBS_DIR=${DEPLOY_ROOT}/jobs`; Docker daemon должен видеть эти же байты
по тому же абсолютному пути. Socket `/var/run/docker.sock` нужен для native
grading. Workspace host и его отдельный daemon этим entry не подключаются.

Данные Gateway в `DEPLOY_ROOT/gateway-data` заранее принадлежат UID/GID
`1000:1000` и доступны ему на запись. Остальные владельцы и режимы файлов
восстанавливаются по исходной установке. Runtime не выполняет скрытый `chown`,
генерацию ключей, grant прав, первичный импорт пользователей или курса.

## Проверка и запуск отдельной установки

Из каталога репозитория выполните только проверку описания:

```sh
docker compose --env-file /private/site-runtime.env -f compose.runtime.yml config --quiet
```

Она не читает содержимое готовых secrets и не обращается к Docker daemon,
но ещё не проверяет наличие bind paths, пригодность image или целостность БД.
Обычный `config` без `--quiet` может раскрыть значения environment; не публикуйте
такой вывод для реальных входов.

БД восстанавливается **до** запуска PL, без работающих писателей. Runtime
проверяет непустой `/var/postgres/PG_VERSION` перед штатным `start-pl.sh`, затем
запускает native support, migrations и приложение. Проверка файла отсеивает
пустой каталог; она не доказывает восстановление пользователей, ответов или
совместимость PostgreSQL. Сначала применяются точные исходные images и ключи;
обновление приложения является отдельной операцией после проверки переноса.

После успешного согласованного restore и проверки всех входов оператор запускает
**новую отдельную** установку:

```sh
docker compose --env-file /private/site-runtime.env -f compose.runtime.yml up -d --wait
docker compose --env-file /private/site-runtime.env -f compose.runtime.yml ps -a
```

Для status, stop, down и повторного up всегда передавайте тот же env-файл и
только этот entry. Не объединяйте его с fixture `compose.yml`, auth или Workspace
overrides: standalone runtime уже содержит auth-сервисы, а те overlays зависят
от fixture bootstrap. `down` сохраняет host bind данные; их нельзя удалять
ради повторного запуска.

Healthcheck Nginx использует `curl --connect-to` и `--noproxy '*'`: соединяется
с `127.0.0.1:443` **внутри своего контейнера**, проверяя прежнее имя/IP и CA.
Публичный origin во время репетиции может всё ещё указывать на старую VM;
этот healthcheck не сможет принять её ответ за готовность новой. `--insecure`
и глобальное изменение доверия не нужны. Readiness не заменяет свежий LTI launch,
native IDs/ответы и Workspace-проверки отдельной приёмки.

## Что ещё требует отдельной сверки

Entry не объявлен готовой заменой сборки действующей VM. Сейчас `/course`
подключён на запись, как в основном Compose для native Git-sync, но никакой
sync при запуске не выполняется. Действующая VM использует generated readonly
курсы, дополнительные catalog/tools/bridge mounts, public JWKS и материалы
с собственными маршрутами Nginx. Их contract и происхождение bytes сверяются
отдельно перед site override; этот entry не монтирует их автоматически.
Прямое применение к прежней VM или её live каталогам не является репетицией.

Тестовая Moodle отсутствует: реальная внешняя Moodle задаётся готовой Gateway
регистрацией. Экзаменационный forward proxy и контроллер также отсутствуют;
будущая интеграция с `exam-lab` будет явной и необязательной. Контроллер не
реализуется до уточнения договора по Windows-пилоту.

Перед следующей фазой проверяют весь учебный путь между курсами, Platform,
Deployment и `exam-lab`, причины расхождений и необходимость дополнительных слоёв.
Независимая рецензия включает возможность удалить дублирование. Разделение
fixture provisioning и steady runtime устраняет обязательный bootstrap при
restore; дальнейшая унификация должна сохранять владельцев данных и обычные
публичные команды. Порядок закреплён в [AGENTS.md](../AGENTS.md).
