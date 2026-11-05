"""推播：Telegram / Discord / ntfy / Email。金鑰一律從環境變數讀取。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

import requests


def _env(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"缺少環境變數：{', '.join(missing)}")
    return [os.environ[k] for k in keys]


def send_telegram(title, body):
    token, chat = _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    text = f"{title}\n\n{body}"[:4000]
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat, "text": text}, timeout=20).raise_for_status()


def send_discord(title, body):
    (url,) = _env("DISCORD_WEBHOOK_URL")
    requests.post(url, json={"content": f"**{title}**\n{body}"[:1990]}, timeout=20).raise_for_status()


def send_ntfy(title, body):
    (topic,) = _env("NTFY_TOPIC")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    requests.post(f"{server}/{topic}", data=body[:4000].encode(),
                  headers={"Title": title.encode().decode("latin-1", "ignore") or "stock-agent",
                           "Markdown": "yes"}, timeout=20).raise_for_status()


def send_email(title, body):
    host, port, user, pw, to = _env("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = title, user, to
    with smtplib.SMTP(host, int(port), timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)


CHANNELS = {"telegram": send_telegram, "discord": send_discord, "ntfy": send_ntfy, "email": send_email}


def short_message(plan) -> tuple[str, str]:
    title = f"投資建議 {plan['as_of']}（{len(plan['orders'])} 筆）"
    lines = []
    for o in sorted(plan["orders"], key=lambda o: o["side"] != "SELL"):
        tag = "賣" if o["side"] == "SELL" else "買"
        lines.append(f"{tag} {o['stock_id']} {o['name']} {o['shares']:,} 股 @~{o['price_ref']}（{o['action']}）")
    if not lines:
        lines.append("今日無買賣建議，持股續抱。")
    lines.append(f"淨值約 {plan['nav']:,}，預估現金 {plan['cash_after_est']:,}")
    lines.append("僅供參考，請自行判斷後手動下單。")
    return title, "\n".join(lines)


def notify(plan, cfg) -> list[str]:
    ncfg = cfg.get("notify", {}) or {}
    if ncfg.get("only_when_trades") and not plan["orders"]:
        return ["skip: no trades"]
    title, body = short_message(plan)
    results = []
    for ch in ncfg.get("channels", []) or []:
        try:
            CHANNELS[ch](title, body)
            results.append(f"{ch}: ok")
        except Exception as e:  # 一個通道失敗不影響其他通道
            results.append(f"{ch}: failed ({e})")
    return results
