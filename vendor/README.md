Официальные архивы загружаются `python3 tools/fetch_vendor.py all` и проверяются
по SHA-512 из `versions.json`. Архивы не коммитятся в Git; release workflow
включает их в клиентские пакеты. LICENSE/NOTICE внутри сохраняются.
