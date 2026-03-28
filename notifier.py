import requests
import logging


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logging.error("Telegram send failed: %s", e)
        return False