# Репозиторий и выпуск пакетов

Репозиторий `networkyeldar/ibatyr-apm` хранит согласованную версию установщиков,
оболочки и документации. Разделять на три репозитория пока не требуется.
Дистрибутивы хранятся в **GitHub Releases**, не большими бинарниками в Git.

## Первая публикация с Mac

Распакуйте source-пакет, перейдите в корень `ibatyr-apm`. Нужны Git и GitHub CLI `gh`,
авторизованные в правильном аккаунте. Проверяйте имя аккаунта перед созданием репозитория.

```bash
gh auth status
git init -b main
git add .
git diff --cached --stat
git commit -m "Prepare iBatyr APM deployment kit 0.5.0-rc1"
gh repo create ibatyr-apm --private --source=. --remote=origin --push
```

Команды предназначены для **нового локального каталога и нового репозитория**.
Если репозиторий уже создан с README, сначала clone его, скопируйте туда содержимое
source-пакета без `.git`, просмотрите diff, commit и push. Не используйте force push.

В Git не должны попадать `.ai_settings.json`, license state, `.pem`, ключи LLM,
логи клиента, wheelhouse, server/agent archives. `.gitignore` уже включён, но
перед первым commit всё равно просмотрите staged files. Ключ подписи лицензий
не нужен ни CI, ни release workflow и не добавляется в GitHub Secrets.

## CI

`Tests and packages` запускает тесты и собирает три онлайн-пакета.
Build artifacts доступны из Actions после успешного запуска.

`Build draft release` запускается при изменении VERSION в main или вручную
через Actions → Run workflow:

1. Проверяет тесты.
2. Загружает upstream по фиксированным URL и проверяет SHA-512.
3. Включает архивы OAP и Java agent в соответствующие пакеты.
4. Создаёт **draft release**, привязанный к текущему commit, с SHA256SUMS.

Перед публикацией проверьте приёмку на VM и дополните release notes фактическими
результатами. Повторный запуск с уже существующим тегом не перезаписывает release:
исправьте версию и создайте следующий RC. Workflow не получает private.pem.

## Клиентская поставка

Клиенту передавайте нужные release assets, SHA256SUMS и public.pem отдельным файлом.
Доступ к private репозиторию с issuer-инструментом не требуется.
Подпись SHA256SUMS ключом релизов и автоматическая SBOM/security policy — отдельный
этап; текущий checksum обеспечивает сверку файлов, а не самостоятельное доказательство
доверия к издателю. Upstream .asc можно дополнительно проверить по Apache KEYS.

## Безопасность GitHub Actions

Workflow имеет read-only permissions для CI и contents:write для
создания draft release. Перед production закрепите actions по проверенным commit SHA,
настройте protected branches и review. Версии зависимостей верхнего уровня закреплены;
полный cross-platform lock с hashes/wheelhouse требует отдельной сборки.
