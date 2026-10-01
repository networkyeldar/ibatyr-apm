# Elasticsearch: установка с нуля на Ubuntu 24.04

Этот сценарий устанавливает **один локальный узел Elasticsearch 8.x** на той же VM,
что OAP и AI-оболочка. Данные сохраняются на диске. Это не HA-кластер.
Установка существующего узла, миграция индексов и обновление ES здесь не выполняются.

## 1. Подготовка VM

Ubuntu 24.04, systemd, sudo, Python 3.12, доступ к Ubuntu APT, artifacts.elastic.co,
Apache archive и PyPI. Для совмещённого пилота ориентир: 4 vCPU, **8 GiB RAM минимум**,
20 GiB свободного диска для установки плюс отдельный запас под данные/retention.
Размеры production рассчитываются по реальному потоку spans, логов и метрик.

```bash
git clone https://github.com/networkyeldar/ibatyr-apm.git
cd ibatyr-apm
sudo apt-get update
sudo apt-get install -y python3-venv openjdk-17-jre-headless ca-certificates
```

Команды далее выполняются из этого каталога. При использовании server-пакета
распакуйте его и перейдите внутрь; shell устанавливается из отдельного shell-пакета.

## 2. Установите Elasticsearch

Зафиксируйте выбранную версию 8.x.y. Пример ниже соответствует версии в документации
Elastic 8.19 на момент подготовки. Установщик требует наличия этой точной версии в APT;
если её нет, он остановится. Не заменяет её другой версией автоматически.
Совместимость OAP 10.1.0 с конкретным patch-релизом нужно подтвердить на стенде.

```bash
sudo python3 install.py elasticsearch --version 8.19.22 --heap-gb 2
```

Установщик:

- Проверяет отсутствие существующего ES/данных, свободные 9200/9300, память и диск.
- Подключает официальный подписанный репозиторий **8.x**, проверяет fingerprint ключа.
- Ставит указанную версию через APT и фиксирует её через `apt-mark hold`.
- Создаёт локальный CA и TLS-сертификат с SAN `localhost` и `127.0.0.1`.
- Включает HTTPS, аутентификацию и TLS транспорта; слушает только `127.0.0.1`.
- Настраивает systemd, одинаковые Xms/Xmx и vm.max_map_count не ниже 1048576.
- Запрашивает новый пароль `elastic` через штатную утилиту Elastic, затем повторный
  ввод этого пароля для создания отдельного пользователя `ibatyr_oap`.
- Сохраняет случайный пароль OAP в `/etc/ibatyr-apm/storage.json` (root, 0600).

Пароль администратора не сохраняется в профиле OAP. У `ibatyr_oap` есть `monitor`,
`manage_index_templates` и права на индексы `ibatyr_*`; он не является superuser.
Право управления шаблонами кластерное — используйте выделенный узел/кластер для iBatyr.

Данные: `/var/lib/elasticsearch`. Логи: `/var/log/elasticsearch`.
TLS: `/etc/elasticsearch/ibatyr-certs`. Исходный конфиг и CA private key:
`/etc/ibatyr-apm/es-install-backup` (root, 0700). Не передавайте CA private key другим клиентам.
Срок сертификатов по умолчанию 1095 дней; планируйте перевыпуск до истечения.

## 3. Подключите OAP

```bash
sudo python3 install.py server --storage elasticsearch \
  --local-elasticsearch --agent-bind 192.0.2.10
```

**Замените `192.0.2.10` приватным IPv4 этой VM.** По ACL разрешите TCP 11800 только
от серверов приложений. На 9200/9300 правила внешнего доступа не нужны.

Установщик импортирует CA в отдельный JKS, включает проверку TLS, устанавливает
namespace `ibatyr` и replicas=0 для одного узла. Пароль `changeit` в JKS защищает
целостность хранилища публичного CA, не закрытый ключ. OAP получает отдельный пароль
из профиля без его передачи через аргументы командной строки.

При добавлении узлов настройте replicas и транспортную безопасность заново;
текущая конфигурация предполагает один узел и не обеспечивает отказоустойчивость.

## 4. Установите оболочку

```bash
sudo python3 install.py shell --public-key /полный/путь/public.pem
```

Для первоначального просмотра без ключа издателя можно опустить `--public-key`.
AI-лицензия настраивается отдельно. Вход через SSH-туннель:

```bash
ssh -N -L 18010:127.0.0.1:8010 USER@SERVER_IP
```

Браузер: `http://127.0.0.1:18010/ai/`. Далее подключите [Java agent](AGENT.md).

## 5. Проверка и сохранность данных

```bash
sudo systemctl status elasticsearch ibatyr-oap ibatyr-apm --no-pager
sudo curl --fail --cacert /etc/elasticsearch/ibatyr-certs/ca.crt \
  --user elastic https://127.0.0.1:9200/_cluster/health
python3 tools/doctor.py
```

`curl --user elastic` запросит пароль; не добавляйте его в команду.
Создайте тестовые HTTP/JDBC запросы, найдите trace и метрики. В согласованное окно
перезапустите ES/OAP и убедитесь, что та же трассировка доступна до окончания retention.
Для single-node OAP indexes replicas=0; `yellow` у другого индекса требует проверки
его replica settings, а не отключения security.

Snapshots Elasticsearch на отдельное хранилище обязательны для восстановления:
копия живого каталога `/var/lib/elasticsearch` не заменяет штатный snapshot.
Настройте retention OAP под диск; не назначайте стороннюю ILM policy без проверки
взаимодействия с очисткой индексов OAP. План обновлений: сначала стенд, затем явно
`apt-mark unhold elasticsearch` в окно обслуживания; пакеты не обновляются автоматически.

## Если установка прервалась

Не удаляйте данные и не запускайте first-install поверх. Проверьте журналы
`journalctl -u elasticsearch` и `/var/log/elasticsearch/ibatyr-apm.log`.
Если сбой был до первого запуска, проверьте также `systemctl is-enabled elasticsearch`:
на этапе установки служба временно masked, чтобы пакет не запустился с чужим конфигом.

Если TLS-служба готова, но пароль/пользователь ещё не настроены и `storage.json`
**не существует**, повторите только шаг настройки:

```bash
sudo /usr/share/elasticsearch/bin/elasticsearch-reset-password -u elastic -i
sudo python3 install.py storage_setup
```

Если профиль или пользователь уже существуют, автоматическая перезапись запрещена.
Сначала проверьте состояние их создания; для восстановления используйте сохранённый
профиль и согласованную смену пароля. Не публикуйте профиль в GitHub/чатах.

## Ограничения поставки

Elasticsearch скачивается из официального APT при установке, не включён в tar.gz.
Полностью offline-поставка требует отдельного зеркала APT/PyPI. Условия поставки ES
определяются Elastic и не заменяются лицензией AI-оболочки.
На целевой VM ещё требуется проверить полную установку, запись OAP, restart,
retention, snapshots и нагрузку. Тесты конфигурации не заменяют эту приёмку.

Источники: [Debian installation 8.19](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/deb.html),
[certutil](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/certutil.html),
[roles API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/security-api-put-role.html),
[users API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/security-api-put-user.html).
