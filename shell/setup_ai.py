import getpass
import json
import secrets
from pathlib import Path

from ai_security import CONFIG_PATH, password_hash, write_config


def main():
    path = Path(CONFIG_PATH)
    old = json.loads(path.read_text()) if path.exists() else {}
    username = input(f"Логин [{old.get('username', 'devadmin')}]: ").strip() or old.get("username", "devadmin")
    password = getpass.getpass("Пароль приложения (минимум 12 символов): ")
    if len(password) < 12:
        raise SystemExit("Пароль слишком короткий. Ничего не изменено.")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпали. Ничего не изменено.")
    salt = secrets.token_hex(16)
    old.update(username=username, password_salt=salt, password_hash=password_hash(password, salt))
    old.setdefault("providers", {
        "external": {"base_url": "", "model": "", "api_key": "", "auth_mode": "bearer", "token_parameter": "max_tokens", "max_tokens": 2048},
        "local": {"base_url": "http://127.0.0.1:8000/v1", "model": "RedHatAI/gemma-4-26B-A4B-it-NVFP4", "api_key": "", "auth_mode": "bearer", "token_parameter": "max_tokens", "max_tokens": 2048},
    })
    write_config(path, old)
    print("Логин настроен. Ключи LLM добавляются после входа в интерфейс.")
    print("После изменения пароля перезапустите Uvicorn.")


if __name__ == "__main__":
    main()
