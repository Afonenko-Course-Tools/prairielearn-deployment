# Контрольная тревога Wazuh

Отдельный тестовый стек использует официальный Wazuh Docker single-node
[v4.14.10](https://github.com/wazuh/wazuh-docker/tree/76ddb88b9c8b618a254a287f2684f93d3c5ac2f4/single-node)
и агент Windows 4.14.10-1. Обычный запуск Deployment и его учебные данные
остаются в своих проектах и томах. Для стенда выделите отдельное имя проекта,
приватный каталог и Linux-адрес, достижимый только из испытательного сегмента.
Docker публикует только TCP 1514 на выбранном адресе и Dashboard на
`127.0.0.1:15601`. На доверенном сетевом узле проверьте отказ из других
сегментов. Dashboard использует тестовые учётные данные upstream и предназначен
для доступа с самого Docker-хоста.

Для одноразовой QEMU VM с пользовательской сетью можно сохранить `restrict=on`
и перед очередным запуском добавить в её `-netdev user` одну явную переадресацию
`guestfwd=tcp:<свободный-виртуальный-IPv4>:1514-tcp:127.0.0.1:1514`.
На Docker-хосте укажите `WAZUH_AGENT_BIND_IP=127.0.0.1`, на агенте — выбранный
виртуальный IPv4. Проверьте соединение с TCP 1514 из VM и недоступность порта с
другого узла. Учитывайте адрес после переадресации при регистрации агента.
[Сеть QEMU](https://www.qemu.org/docs/master/system/invocation.html)

Нужны Docker Compose 2.24.4+ (операторы `!override` и `!reset`), минимум
8 ГБ RAM и 50 ГБ диска по требованиям Wazuh и `vm.max_map_count >= 262144`.
Проверьте доступную память перед запуском вместе с Windows и PrairieLearn.
Сгенерированные TLS-ключи, ключ агента и тома хранятся вне Git.

В корне Deployment задайте собственные абсолютные пути и адрес изолированного
сегмента. Для повторного пуска используйте те же каталоги и имя проекта.
Первичная подготовка и загрузка образов выполняются один раз.

```sh
export WAZUH_PROBE_DIR="$PWD/deploy/wazuh"
export WAZUH_PROBE_STATE=/absolute/private/wazuh-probe
export WAZUH_AGENT_BIND_IP=192.0.2.10  # замените адресом испытательного сегмента
umask 077
mkdir -p "$WAZUH_PROBE_STATE"
git clone --depth 1 --branch v4.14.10 https://github.com/wazuh/wazuh-docker.git "$WAZUH_PROBE_STATE/upstream"
test "$(git -C "$WAZUH_PROBE_STATE/upstream" rev-parse HEAD)" = 76ddb88b9c8b618a254a287f2684f93d3c5ac2f4
cd "$WAZUH_PROBE_STATE/upstream/single-node"
cat config/wazuh_cluster/wazuh_manager.conf "$WAZUH_PROBE_DIR/active-response.xml" > "$WAZUH_PROBE_STATE/wazuh_manager.conf"

docker compose -p exam-wazuh-probe -f generate-indexer-certs.yml pull
docker compose -p exam-wazuh-probe -f docker-compose.yml -f "$WAZUH_PROBE_DIR/compose.probe.yml" pull
docker compose -p exam-wazuh-probe -f generate-indexer-certs.yml run --rm --pull never generator
docker compose -p exam-wazuh-probe -f docker-compose.yml -f "$WAZUH_PROBE_DIR/compose.probe.yml" config --quiet
docker compose -p exam-wazuh-probe -f docker-compose.yml -f "$WAZUH_PROBE_DIR/compose.probe.yml" up -d --pull never
```

Перед пробой проверьте состояние трёх контейнеров, готовность Dashboard и
конфигурацию менеджера (`/var/ossec/bin/wazuh-analysisd -t` и
`/var/ossec/bin/wazuh-execd -t` внутри `wazuh.manager`). Зарегистрируйте
один агент с адресом тестовой Windows через штатный `manage_agents`,
передайте ключ приватно оператору и проверьте состояние Active.
Порт автоматической регистрации 1515 закрыт на границе хоста. Оператор
устанавливает агент и действие из поставки exam-lab на одноразовой Windows
и использует только это подключение.

Источник контрольного события — журнал Windows `Application`, поставщик
`ExamLab-Alert`, ID `6101`. Оператор пишет событие штатным средством Windows
с идентификатором текущей пробы после её взведения. Правило `110610`
сопоставляет все три поля; штатный Active Response вызывает установленный
`exam-lab-alert.cmd` на агенте события. Проверка принадлежности тревоги
текущей пробе и сеансу выполняется Windows-компонентом.

Для результата запишите время, имя/ID агента, идентификатор записи Windows,
идентификатор текущей пробы, правило и идентификатор тревоги из
`/var/ossec/logs/alerts/alerts.json`; подтвердите ровно одно совпадение и
локальный показ по инструкции exam-lab. При повторной проверке сделайте
`down` и `up -d` с теми же параметрами, убедитесь в сохранности агента
и прежней тревоги. Из того же каталога и с теми же переменными остановите
только проект Wazuh. Тома сохраняются.

```sh
docker compose -p exam-wazuh-probe -f docker-compose.yml -f "$WAZUH_PROBE_DIR/compose.probe.yml" down
```
