import os

import requests


def send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram] credentials missing, printing instead:\n" + text)
        return False
    # Telegram limit is 4096 chars; split on line breaks
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    ok = True
    for c in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": c, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if not r.ok:
            print("[telegram] error:", r.text)
            ok = False
    return ok
