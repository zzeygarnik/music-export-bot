"""
Получение VK токена через VK iPhone client.
Запуск: python get_vk_token_iphone.py
"""
import urllib.request
import urllib.parse
import json
import sys

CLIENT_ID = "3140623"
CLIENT_SECRET = "VeWdmVclDCtn6ihuP1BO"
USER_AGENT = "VKiPhone/5.50 (iPhone; iOS 12.0; Scale/2.00)"

def get_token(login: str, password: str) -> str | None:
    params = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": login,
        "password": password,
        "scope": "audio,offline",
        "v": "5.131",
    }).encode()

    req = urllib.request.Request(
        "https://oauth.vk.com/token",
        data=params,
        headers={"User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        data = json.loads(e.read())

    if "access_token" in data:
        return data["access_token"]
    print(f"❌ Ошибка: {data.get('error')}: {data.get('error_description', '')}")
    if data.get("redirect_uri"):
        print(f"   Требует подтверждения: {data['redirect_uri']}")
    return None


if __name__ == "__main__":
    login = input("Телефон VK: ").strip()
    password = input("Пароль VK: ").strip()
    token = get_token(login, password)
    if token:
        print(f"\n✅ Токен получен (VK iPhone):\n{token}")
        print("\nДобавь в .env:\nVK_TOKEN=" + token)
    else:
        print("\n❌ Оба client не работают. VK заблокировал аккаунт.")
        sys.exit(1)
