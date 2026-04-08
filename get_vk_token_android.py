"""
Получение VK токена через VK Android client (не Kate Mobile).
Запуск: python get_vk_token_android.py
Токен положить в .env: VK_TOKEN=...
"""
import urllib.request
import urllib.parse
import json
import sys

# VK Android app credentials
CLIENT_ID = "2274003"
CLIENT_SECRET = "hHbZxrka2uZ6jB1inYsH"
USER_AGENT = "VKAndroidApp/5.52-4543 (Android 5.1.1; SDK 22; x86_64; unknown Android SDK built for x86_64; en; 320x240)"

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
        print(f"\n✅ Токен получен (VK Android):\n{token}")
        print("\nДобавь в .env:\nVK_TOKEN=" + token)
    else:
        print("\n💡 Попробуй VK iPhone client — запусти get_vk_token_iphone.py")
        sys.exit(1)
