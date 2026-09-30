# iBatyr APM 0.4.0-rc1

- Самостоятельный backend вместо зависимости от legacy main.py.
- Установщики shell/server/agent для Ubuntu 24.04; Docker не требуется.
- Проверка занятых портов и запрет перезаписи existing deployment.
- Подключение к существующему OAP или новая OAP 10.1.0 с внешним ES / demo H2.
- Agent package 9.3.0, без автоматического рестарта JVM клиента.
- Три дистрибутива, контрольные суммы, CI и ручной draft release workflow.
- Сохранены dashboard, auth/CSRF, AI preview, offline trial/paid.

Это RC для стенда. Результаты и непройденные проверки: docs/VALIDATION.md.
Архивные upstream-версии зафиксированы для воспроизводимости, не объявлены
актуальными безопасными версиями. Review зависимостей и upgrade policy нужны до продажи.

Проверено: 29 unit/integration tests; browser со stub OAP; systemd unit syntax;
реальный OAP 10.1.0 + GraphQL; запуск агента 9.3.0 с Java 17 и получение JDBC spans.
