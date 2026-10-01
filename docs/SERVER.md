# Сервер и постоянное хранилище

## Ресурсы стенда

Ориентир для теста: 4 vCPU, 8 GB RAM, 20 GB свободного диска без учёта хранилища.
Это не оценка production-нагрузки: размеры зависят от числа spans, sampling и retention.

## Существующий monitoring

Не запускайте `install.py server` на VM с работающим OAP на 12800/11800.
Подключите оболочку к существующему GraphQL. Новую оболочку можно проверить
параллельно на 8011 и импортировать настройки, сохраняя UUID лицензии:

```bash
sudo python3 install.py shell --port 8011 \
  --oap http://127.0.0.1:12800/graphql \
  --import-config /home/devadmin/skywalking-ai/backend/.ai_settings.json \
  --import-license-dir /home/devadmin/skywalking-ai/backend/.ibatyr_license
```

Пути относятся к ранее настроенной установке. Если каталога лицензии ещё нет,
опустите `--import-license-dir`. Импорт предназначен только для переноса **той же
установки**, не для клонирования лицензии клиентам. Если `ibatyr-apm.service` уже
создан, установщик остановится: используйте отдельную тестовую VM.

Туннель: `ssh -N -L 18011:127.0.0.1:8011 devadmin@100.72.85.197`.
Проверьте браузером новую сборку до переключения с прежней.

## Elasticsearch ещё не установлен

Следуйте [полной инструкции установки Elasticsearch](ELASTICSEARCH.md): новый
`install.py elasticsearch` создаёт локальный HTTPS-узел с постоянными данными,
затем `server --storage elasticsearch --local-elasticsearch` подключает OAP.

## Чистая клиентская VM, существующее Elasticsearch

Подготовьте совместимое постоянное Elasticsearch 7/8 по матрице OAP 10.1.0.
В server-архив сам Elasticsearch не включён; новый установщик скачивает его из APT. Для существующего кластера: его лицензия, sizing, резервное копирование,
кластеризация и обновление требуют отдельной настройки. Не подключайте OAP
к хранилищу другого клиента. Используйте отдельный storage endpoint/учётную запись.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv openjdk-17-jre-headless ca-certificates
sudo python3 install.py server --storage elasticsearch \
  --es-nodes 192.0.2.20:9200 --es-protocol https --es-user ibatyr \
  --agent-bind 192.0.2.10
sudo python3 install.py shell --public-key ./publisher-public.pem
```

Замените оба IP и имя пользователя. Пароль ES запрашивается скрыто и сохраняется
в `/etc/ibatyr-apm/server.env` (0600). HTTPS проверяет доверенную Java цепочку
сертификатов; для частного CA заранее настройте Java truststore.
Установщик не отключает проверку TLS. У storage-аккаунта должны быть разрешения
на необходимые индексы/шаблоны; неверные права отражаются в журнале OAP.

## Службы, порты, данные

| Компонент | Путь / служба | Доступ |
|---|---|---|
| OAP | `/opt/ibatyr/server/10.1.0`, `ibatyr-oap.service` | gRPC 11800: заданный IPv4 |
| GraphQL | OAP 12800 | Только loopback |
| PromQL / LogQL / Firehose | 9090 / 3100 / 12801 | Только loopback |
| Оболочка | `/opt/ibatyr/shell/0.5.0-rc1`, `ibatyr-apm.service` | Только loopback 8010 |
| Настройки | `/etc/ibatyr-apm/` | root |
| Логин, API-ключи, лицензия | `/var/lib/ibatyr-apm/` | пользователь службы ibatyr |

Оригинальный UI на 8080 установщик не запускает: интерфейс оператора — наша оболочка.
Защитите 11800 сетевым ACL: только известные серверы приложений. gRPC в этом
установщике без TLS; для недоверенной сети требуется отдельно проверенная
TLS/mTLS-конфигурация. На публичный интерфейс без защиты порт не выставляйте.

## Проверка

```bash
sudo systemctl status ibatyr-oap ibatyr-apm --no-pager
python3 tools/doctor.py
sudo journalctl -u ibatyr-oap -n 80 --no-pager
```

Пустой список сервисов сразу после установки нормален: подключите агент и
создайте тестовые запросы к приложению. После появления данных проверьте
дашборд, поиск по дате и одну трассировку. Schema doctor использует отдельные
короткие introspection-запросы, а не отключение защиты GraphQL.

Источники: [OAP 10.1.0 setup](https://skywalking.apache.org/docs/main/v10.1.0/en/setup/backend/backend-setup/),
[configuration](https://skywalking.apache.org/docs/main/v10.1.0/en/setup/backend/configuration-vocabulary/).
