# Переименование инфраструктурного репозитория

Существующий `Afonenko-Course-Tools/prairielearn-local` переименован в
`Afonenko-Course-Tools/prairielearn-deployment` с сохранением Git-истории.
Это изменение имени и владельца документации о развёртывании, а не миграция
действующей VM. План отдельной VM находится в
[инструкции перехода](parallel-vm-migration.md).

Исходная проверка выполнена по GitHub HEAD `159c972` от 15 ноября 2026 года
и локальному checkout этой редакции. Ниже приведены последствия, которые
проверяются при обновлении ссылок; секреты и пользовательский state не читались.

## Ссылки и имена, которые нужно различать

| Область                                                      | Решение                                                                                                                                                                                                          |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GitHub URL, `origin`, clone/cd в README и build-delivery     | Использовать новое имя репозитория. Историю не копировать в новый пустой repo и force push не применять.                                                                                                         |
| Заголовки workflow, Task descriptions и текущая документация | Называть владельца Deployment; слово «локальный» сохраняется для конкретного preview/fixture.                                                                                                                    |
| `.gitmodules` этого repo                                     | `deps/platform` продолжает ссылаться на `prairielearn-platform`; его имя и gitlink не меняются из-за переименования consumer.                                                                                    |
| Default Compose project                                      | Старое `prairielearn-local` — идентичность существующих ресурсов, а не ошибка ссылки. Сохранить совместимый default или явно прежнее `COMPOSE_PROJECT_NAME`; не менять его механической заменой.                 |
| Test Moodle image tag                                        | `${COMPOSE_PROJECT_NAME}-moodle:5.3` зависит от выбранного project. Переименование каталога/репозитория само по себе не требует смены image/runtime.                                                             |
| Test Moodle records                                          | `idnumber=prairielearn-local`, маркеры `prairielearn-local-*` и название tool используются повторным bootstrap. Без отдельной миграции записи сохраняются, иначе могут появиться дубликаты course/tool/activity. |
| Preview projects                                             | `pl-preview-java` и `pl-preview-system-practice`, их state/jobs и stable course paths сохраняются. Они не производны от имени репозитория.                                                                       |
| Абсолютные пути                                              | Существующие state, jobs, configs, secrets и site roots задаются явно. Clone в новый каталог не переносит их и не должен незаметно создавать пустой state.                                                       |
| Исторические отчёты                                          | Старое имя в описании испытания 11 ноября, старых планах и evidence допустимо как факт истории. Не выдавать это за актуальную clone-инструкцию.                                                                  |

В исходном составе точные `prairielearn-local` найдены в README,
`docs/build-delivery.md`, `compose.yml` и `scripts/moodle-bootstrap.php`.
Обозначение владельца `Local` встречается также в AGENTS, Taskfile, workflow,
scripts/quality, prepare/preview и документации. Это разные по смыслу изменения:
runtime identifiers нельзя заменить вместе с пользовательскими подписями.

## Workflow и потребители

В репозитории один проверенный workflow `.github/workflows/check.yml`.
Он запускается на `main`, PR и вручную, использует `actions/checkout` текущего
репозитория, его submodules и `go-task check`. В нём нет жёсткой ссылки на старый
GitHub owner/repository. Обозначения `Проверки Local` и `local-check-*` — подпись
и concurrency group; их изменение не является изменением Compose project.
Сам workflow не публикует образы с именем этого репозитория.

Java и System Practice остаются внешними `COURSE_ROOT`, Platform выбирается
gitlink или явным `PLATFORM_ROOT`. Проверенный Java `.gitmodules` ссылается
на Platform и Performance, не на Deployment. GitHub code search по старому
имени в Java, System-Practice, Platform и Core 15 ноября совпадений не вернул;
это ограниченный индексный поиск, а не доказательство отсутствия любых внешних
checkout, bookmarks, workflow integrations или неопубликованных документов.

Для известных внешних потребителей обновляются прямые Git URLs, clone-команды,
ссылки документации и возможные submodule URLs. Выбранные commits и installed
Quarto packages от переименования Deployment не меняются. Следует проверить
GitHub settings существующего repo, связи PR/workflows и доступы через новое
имя штатными средствами; значения secrets при этом не выводятся и заново
не создаются только ради смены имени.

## Проверка результата

Проверить новый `origin`, доступность истории и прежнего HEAD, сбор workflow
по новой ссылке и обычный `go-task check`. В новом чистом clone получать
`deps/platform` стандартным `git submodule update --init`.
Сверить текущие clone/документальные ссылки поиском старого имени, отделяя
сохранённые runtime/fixture identifiers и исторические записи.

Перед любым runtime-запуском выбрать явные прежние либо новые project/state.
Переименование не разрешает `down -v`, удаление state, сброс Moodle registrations,
native IDs, оценок или Workspace. Локальный preview и перенос production
принимаются отдельно; успешная статическая проверка не подтверждает миграцию VM.
