#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Google News RSS → Telegram / Slack 알림
- Telegram: 항상 발송
- Slack: 업무시간(평일 06~20시 KST, 비공휴일)에만 발송, 3시간에 한 번만 발송
"""

import os
import json
import re
import requests
import feedparser
from urllib.parse import quote_plus, urlparse
from datetime import datetime, timedelta
from calendar import timegm
import pytz
import holidays

# =========================
# 기본 설정
# =========================

SENT_FILE = os.environ.get("SENT_STATE_PATH", ".sent_articles.json")
SLACK_THROTTLE_FILE = os.environ.get("SLACK_THROTTLE_STATE_PATH", ".last_slack_sent.json")
SLACK_THROTTLE_HOURS = 3
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


def load_last_slack_sent_utc():
    """마지막 슬랙 발송 시각(UTC epoch) 반환. 없으면 0."""
    if os.path.exists(SLACK_THROTTLE_FILE):
        try:
            with open(SLACK_THROTTLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return float(data.get("last_sent_utc", 0))
        except Exception:
            pass
    return 0.0


def save_last_slack_sent_utc(utc_epoch):
    with open(SLACK_THROTTLE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_sent_utc": utc_epoch}, f)


def can_send_slack_now():
    """3시간에 한 번만 슬랙 발송 가능 여부."""
    now_utc = datetime.now(pytz.timezone("UTC")).timestamp()
    last = load_last_slack_sent_utc()
    return (now_utc - last) >= (SLACK_THROTTLE_HOURS * 3600)


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
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"  [Slack] HTTP {response.status_code} body: {response.text[:200]}")
        return response.status_code == 200
    except Exception as e:
        print(f"  [Slack] 전송 예외: {e}")
        return False


# =========================
# 뉴스 처리
# =========================

# 기본 키워드 (환경변수 없을 때 사용)
DEFAULT_KEYWORD = "네이버,스테이블코인,삼성전자,넥슨"
DEFAULT_SLACK_KEYWORDS = "네이버,스테이블코인"

MAX_ARTICLES_PER_KEYWORD = 5

BLOCKED_SOURCES = {"platea magazine"}

# 단일 단어가 아닌 투자 판단 유도형 복합 표현만 차단한다.
INVESTMENT_PROMOTION_PATTERNS = (
    r"목표\s*주가",
    r"목표가\s*(상향|하향|제시)",
    r"(강력\s*)?매수\s*(추천|의견)",
    r"투자\s*의견\s*(매수|강력\s*매수)",
    r"(오늘의\s*)?급등주",
    r"상승\s*여력",
    r"(저평가|유망)\s*(주|주식|종목)",
    r"주가\s*(전망|예측)",
    r"(주식|종목)\s*(추천|분석)",
    r"매수\s*(기회|타이밍)",
    r"price\s*target",
    r"(stocks?|shares?)\s*to\s*buy",
    r"(strong\s*)?buy\s*(rating|recommendation)",
)

# 최근 N시간 이내 발행 기사만 수집 (과거 기사 제외)
RECENT_HOURS = int(os.environ.get("RECENT_HOURS", "1"))


def get_article_published_utc(entry):
    """기사 발행 시각을 UTC datetime으로 반환 (필터용). 없으면 None."""
    utc = pytz.timezone("UTC")
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime.fromtimestamp(timegm(entry.published_parsed), tz=utc)
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime.fromtimestamp(timegm(entry.updated_parsed), tz=utc)
        published = entry.get("published") or entry.get("updated")
        if published:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(published)
            if parsed.tzinfo is None:
                parsed = utc.localize(parsed)
            return parsed.astimezone(utc)
    except Exception:
        pass
    return None


def get_article_time_kst(entry):
    """기사 발행 시각을 KST HH:MM으로 반환"""
    utc = pytz.timezone("UTC")
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime.fromtimestamp(timegm(entry.published_parsed), tz=utc)
            return dt.astimezone(KST).strftime("%H:%M")
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            dt = datetime.fromtimestamp(timegm(entry.updated_parsed), tz=utc)
            return dt.astimezone(KST).strftime("%H:%M")
        published = entry.get("published") or entry.get("updated")
        if published:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(published)
            if parsed.tzinfo is None:
                parsed = utc.localize(parsed)
            return parsed.astimezone(KST).strftime("%H:%M")
    except Exception:
        pass
    return "시간 미상"


def get_article_source(entry):
    """기사 출처명 반환 (feedparser source 또는 링크 도메인)"""
    try:
        source = entry.get("source")
        if isinstance(source, dict) and source.get("title"):
            return source["title"].strip()
        if hasattr(source, "title"):
            return getattr(source, "title", "").strip() or ""
        link = entry.get("link") or entry.get("id") or ""
        if link:
            host = urlparse(link).netloc or ""
            if host.startswith("www."):
                host = host[4:]
            return host or "출처 미상"
    except Exception:
        pass
    return "출처 미상"


def get_article_exclusion_reason(entry):
    """제외 대상이면 사유 문자열, 통과 대상이면 None을 반환."""
    source = get_article_source(entry).strip()
    normalized_source = " ".join(source.casefold().split())
    if normalized_source in BLOCKED_SOURCES:
        return f"차단 출처: {source}"

    title = (entry.get("title") or "").strip()
    # RSS 출처 정보가 누락돼도 제목에 출처명이 붙은 경우 차단한다.
    if "platea magazine" in title.casefold():
        return "차단 출처명 포함: Platea Magazine"

    for pattern in INVESTMENT_PROMOTION_PATTERNS:
        if re.search(pattern, title, flags=re.IGNORECASE):
            return f"투자 홍보/분석 제목 패턴: {pattern}"
    return None


def format_digest(keyword, entries, for_telegram=False):
    """
    키워드별 뉴스 요약 메시지 생성
    형식: 🗞️ [뉴스 요약] 키워드
          1) 제목 - 출처 | HH:MM
    """
    lines = [f"🗞️ [뉴스 요약] {keyword}", ""]
    for idx, entry in enumerate(entries, start=1):
        title = entry.get("title", "제목 없음")
        link = entry.get("link") or entry.get("id") or ""
        time_str = get_article_time_kst(entry)
        if for_telegram:
            # HTML: 제목을 링크로 (출처는 제목에 포함되는 경우가 있어 생략)
            line = f'{idx}) <a href="{link}">{title}</a> | {time_str}'
        else:
            # Slack: <url|title> 형식 (제목에 | 있으면 파싱 깨짐 → 치환)
            safe_title = (
                title.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("|", "¦")
            )
            safe_link = link.replace(">", "›")
            line = f"{idx}) <{safe_link}|{safe_title}> | {time_str}"
        lines.append(line)
        lines.append("")  # 기사 간 줄바꿈
    return "\n".join(lines).rstrip()


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
    slack_sent_this_run = False

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
        cutoff_time = datetime.now(pytz.timezone("UTC")) - timedelta(hours=RECENT_HOURS)
        new_articles = []
        for entry in entries:
            link = entry.get("link") or entry.get("id") or ""
            if not link or link in sent_articles:
                continue
            published_time = get_article_published_utc(entry)
            if published_time is None or published_time < cutoff_time:
                continue
            exclusion_reason = get_article_exclusion_reason(entry)
            if exclusion_reason:
                title = entry.get("title", "제목 없음")
                print(f"  [{keyword}] 제외: {exclusion_reason} | {title}")
                continue
            new_articles.append(entry)

        if not new_articles:
            print(f"  [{keyword}] 새 뉴스 없음")
            continue

        digest_entries = new_articles[:MAX_ARTICLES_PER_KEYWORD]
        print(f"  [{keyword}] 새 뉴스 {len(digest_entries)}건 요약 발송")

        # 슬랙 발송 여부: 이 키워드가 슬랙용 목록에 있고, 웹훅이 있을 때만
        send_to_slack = bool(slack_webhooks) and (keyword.strip().lower() in slack_keywords)

        # 키워드별 요약 메시지 1통 (🗞️ [뉴스 요약] 키워드 + 1) 제목 - 출처 | HH:MM)
        telegram_message = format_digest(keyword, digest_entries, for_telegram=True)
        slack_message = format_digest(keyword, digest_entries, for_telegram=False)

        # Telegram: 모든 키워드 발송 (요약 1통)
        if telegram_token and chat_id:
            ok = send_telegram(telegram_token, chat_id, telegram_message)
            if not ok:
                print(f"  ❌ Telegram 전송 실패 [{keyword}]")

        # Slack: 슬랙용 키워드이고 업무시간일 때만 (요약 1통), 3시간에 한 번만
        if send_to_slack:
            if not is_business_time():
                print(f"  ⏸️ Slack 발송 제한 시간 - 드랍 [{keyword}]")
            elif not can_send_slack_now():
                print(f"  ⏸️ Slack 3시간 제한 - 드랍 [{keyword}]")
            else:
                for webhook in slack_webhooks:
                    ok = send_slack(webhook, slack_message)
                    if not ok:
                        print(f"  ❌ Slack 전송 실패 [{keyword}]")
                slack_sent_this_run = True

        for entry in digest_entries:
            link = entry.get("link") or entry.get("id") or ""
            if link:
                sent_articles.append(link)

    if slack_sent_this_run:
        save_last_slack_sent_utc(datetime.now(pytz.timezone("UTC")).timestamp())

    save_sent_articles(sent_articles)


if __name__ == "__main__":
    main()
