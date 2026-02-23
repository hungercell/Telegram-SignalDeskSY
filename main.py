#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Google News RSS → Telegram / Slack 알림
- Telegram: 항상 발송
- Slack: 업무시간(평일 06~20시 KST, 비공휴일)에만 발송
"""

import os
import json
import requests
import feedparser
from urllib.parse import quote_plus
from datetime import datetime
import pytz
import holidays

# =========================
# 기본 설정
# =========================

SENT_FILE = os.environ.get("SENT_STATE_PATH", ".sent_articles.json")
KST = pytz.timezone("Asia/Seoul")
kr_holidays = holidays.KR()

# =========================
# 유틸
# =========================


def load_sent_articles():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "sent" in data:
                return list(data["sent"].keys())
            return []
    return []


def save_sent_articles(data):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def is_business_time():
    now = datetime.now(KST)
    weekday = now.weekday()
    hour = now.hour

    if weekday >= 5:  # 토, 일
        return False

    if now.date() in kr_holidays:
        return False

    if hour >= 20 or hour < 6:
        return False

    return True


# =========================
# Telegram
# =========================


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=data, timeout=10)
    return response.status_code == 200


# =========================
# Slack
# =========================


def get_slack_webhooks():
    urls_env = os.environ.get("SLACK_WEBHOOK_URLS")
    if urls_env:
        urls = [u.strip() for u in urls_env.split(",") if u.strip()]
        return urls
    single = os.environ.get("SLACK_WEBHOOK_URL")
    if single:
        return [single]
    return []


def send_slack(webhook_url, message):
    payload = {"text": message}
    response = requests.post(webhook_url, json=payload, timeout=10)
    return response.status_code == 200


# =========================
# 뉴스 처리
# =========================

# 기본 키워드 (환경변수 없을 때 사용)
DEFAULT_KEYWORD = "네이버,스테이블코인,삼성전자,넥슨"
DEFAULT_SLACK_KEYWORDS = "네이버,스테이블코인"

MAX_ARTICLES_PER_KEYWORD = 5


def main():
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    keyword_str = os.environ.get("KEYWORD", "").strip() or DEFAULT_KEYWORD
    slack_keywords_str = (os.environ.get("SLACK_KEYWORDS", "") or "").strip() or DEFAULT_SLACK_KEYWORDS

    # 텔레그램용 + 뉴스 수집용: 여러 키워드
    keywords = [k.strip() for k in keyword_str.split(",") if k.strip()]
    # 슬랙용: 이 목록에 있는 키워드만 슬랙 발송
    slack_keywords = {k.strip().lower() for k in slack_keywords_str.split(",") if k.strip()}

    slack_webhooks = get_slack_webhooks()

    print(f"키워드(뉴스+텔레그램): {keywords}")
    print(f"키워드(슬랙만): {list(slack_keywords)}")
    print(f"웹훅 URL 개수: {len(slack_webhooks)}")

    sent_articles = load_sent_articles()

    if not keywords:
        print("KEYWORD 없음")
        return

    for keyword in keywords:
        encoded = quote_plus(keyword)
        feed_url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  [{keyword}] RSS 파싱 실패: {e}")
            continue

        entries = getattr(feed, "entries", None) or []
        new_articles = []
        for entry in entries:
            link = entry.get("link") or entry.get("id") or ""
            if link and link not in sent_articles:
                new_articles.append(entry)

        if not new_articles:
            print(f"  [{keyword}] 새 뉴스 없음")
            continue

        print(f"  [{keyword}] 새 뉴스 {len(new_articles)}건 발견")

        # 슬랙 발송 여부: 이 키워드가 슬랙용 목록에 있고, 웹훅이 있을 때만
        send_to_slack = bool(slack_webhooks) and (keyword.strip().lower() in slack_keywords)

        for entry in new_articles[:MAX_ARTICLES_PER_KEYWORD]:
            title = entry.get("title", "제목 없음")
            link = entry.get("link") or entry.get("id") or ""

            message = f"<b>{title}</b>\n{link}"

            # Telegram: 모든 키워드 발송
            if telegram_token and chat_id:
                ok = send_telegram(telegram_token, chat_id, message)
                if not ok:
                    print(f"  ❌ Telegram 전송 실패 [{keyword}]")

            # Slack: 슬랙용 키워드이고 업무시간일 때만
            if send_to_slack:
                if is_business_time():
                    for webhook in slack_webhooks:
                        ok = send_slack(webhook, f"{title}\n{link}")
                        if not ok:
                            print(f"  ❌ Slack 전송 실패 [{keyword}]")
                else:
                    print(f"  ⏸️ Slack 발송 제한 시간 - 드랍 [{keyword}]")

            sent_articles.append(link)

    save_sent_articles(sent_articles)


if __name__ == "__main__":
    main()
