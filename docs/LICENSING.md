# Выпуск и активация лицензий

**Закрытый ключ остаётся у издателя.** Клиент получает публичный ключ и подписанную
лицензию для своей установки. AI-лицензия не отменяет права на Apache-компоненты.

## Компьютер издателя (Mac / Linux)

Из полного репозитория, Python 3.12 рекомендуется:

```bash
python3 -m venv .vendor-venv
.vendor-venv/bin/python -m pip install --upgrade pip
.vendor-venv/bin/python -m pip install --only-binary=:all: -r tools/requirements.txt
.vendor-venv/bin/python tools/issuer.py keygen --directory ~/ibatyr-vendor/keys
```

Если ключи уже созданы ранее, **повторять keygen не нужно**. Старые ключи v0.3
совместимы. `private.pem` зашифрован паролем; резервная копия обязательна.
Не сохраняйте private.pem, пароль или лицензии в GitHub.

## На клиентском сервере

При первой установке передайте `--public-key /path/to/public.pem`.
Если оболочка уже установлена без ключа, скопируйте **публичный** ключ в доступное
службе место, затем выполните:

```bash
sudo install -m 0644 ./publisher-public.pem /var/lib/ibatyr-apm/publisher-public.pem
sudo -u ibatyr env \
  SW_AI_CONFIG=/var/lib/ibatyr-apm/settings.json \
  IBATYR_LICENSE_DIR=/var/lib/ibatyr-apm/license \
  /opt/ibatyr/shell/0.4.0-rc1/.venv/bin/python \
  /opt/ibatyr/shell/0.4.0-rc1/setup_license.py \
  --public-key /var/lib/ibatyr-apm/publisher-public.pem
```

В интерфейсе «Лицензия» скачайте запрос активации. Он содержит UUID, не пароль.

## Снова у издателя

```bash
.vendor-venv/bin/python tools/issuer.py issue \
  --private-key ~/ibatyr-vendor/keys/private.pem \
  --request ~/Downloads/ibatyr-activation-request.json \
  --customer 'Customer name' --edition trial --days 30 \
  --output ~/Downloads/customer-trial.json
```

Для годовой лицензии: `--edition professional --days 365`; при необходимости
`--grace-days 7`. Загрузите файл в UI клиента. Срок идёт от выпуска, не от первого
импорта. Продление — новый файл для прежнего installation_id, без рестарта.

Trial: 1..30 дней; paid: выбранный срок. После expiry выключается AI, остаются
метрики/трассировки/экспорт. Контроля числа агентов, онлайн-отзыва, аппаратной
привязки нет. Root клиента может изменить Python-код: offline-лицензия не является
абсолютной защитой от вмешательства. Полное клонирование VM сохраняет UUID.
