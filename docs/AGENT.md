# iBatyr APM agent

Пакет содержит официальный Java agent 9.3.0 с исходными лицензиями и плагинами.
Внутренний JAR остаётся `skywalking-agent.jar`. Ребрендинг пакета не меняет протокол
и имена Java-классов. Не устанавливайте второй экземпляр поверх действующего агента.

## Установка

Целевой сервер приложения в этом установщике — Ubuntu 24.04. Нужен реальный
пользователь, от которого работает JVM. Агент не является отдельной systemd-службой.

```bash
sudo python3 install.py agent \
  --collector 192.0.2.10:11800 \
  --service-name MyApplication \
  --app-user appuser
```

Установщик проверяет SHA-512, распаковывает агент в `/opt/ibatyr/agent/MyApplication`
и даёт пользователю приложения права записи только в каталог `logs`.
Конфигурация и JAR доступны для чтения. Имя сервиса и адрес проверяются и передаются
как JVM options; параметры подключения не нужно искать в исходной конфигурации.

## Подключение к обычному Java-приложению

В выводе установщика и файле `ibatyr-java-options.txt` будет строка. Пример:

```bash
java \
  -javaagent:/opt/ibatyr/agent/MyApplication/skywalking-agent.jar \
  -Dskywalking.agent.service_name=MyApplication \
  -Dskywalking.collector.backend_service=192.0.2.10:11800 \
  -jar /path/to/application.jar
```

Это пример запуска, не команда для копирования без замены пути приложения.
Используйте JVM приложения: установка агента не должна менять её версию.

## Приложение под systemd

Удобно добавить `JAVA_TOOL_OPTIONS` через override конкретной службы:

```ini
[Service]
Environment="JAVA_TOOL_OPTIONS=-javaagent:/opt/ibatyr/agent/MyApplication/skywalking-agent.jar -Dskywalking.agent.service_name=MyApplication -Dskywalking.collector.backend_service=192.0.2.10:11800"
```

Сначала проверьте существующие `JAVA_TOOL_OPTIONS` и `ExecStart`: не затрите
другие JVM-флаги, не добавляйте агент дважды. Затем `systemctl daemon-reload`
и рестарт **вашей службы приложения** в согласованное окно. Установщик её не трогает.
Для Tomcat используйте предусмотренные его поставкой JVM options/setenv.sh;
для контейнера требуется отдельное подключение каталога агента и JVM options.

## Приёмка

1. С сервера приложения доступен TCP 11800 целевого OAP.
2. JVM стартует без ошибок загрузки javaagent/plugins.
3. В логах агента нет повторяющихся ошибок соединения.
4. После тестовых HTTP-запросов сервис появился в GENERAL.
5. В одной трассировке виден HTTP Entry и ожидаемые JDBC/HTTP Exit spans.
6. Сравните latency/CPU/RAM приложения до/после на тестовой нагрузке.

SQL-текст появляется только при поддерживаемом JDBC-плагине и записанных spans.
Не включайте запись параметров SQL/паролей ради AI. Совместимость с конкретным
JDK, контейнером и JDBC-драйвером клиента требует отдельного smoke test.

[Официальная документация Java agent](https://skywalking.apache.org/docs/skywalking-java/latest/en/setup/service-agent/java-agent/readme/)
