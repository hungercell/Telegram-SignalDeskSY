#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Google News RSS → 키워드별 기사 묶음
Telegram / Slack 동일한 요약 포맷으로 전송
"""

import os
import json
import html
import requests
import feedparser
from urllib.parse import quote_plus
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from calendar import timegm
from typing import List, Dict


# =========================
# Slack
# =========================
def send_slack_message(webhook_url: str, text: str):
    payload = {"text": text}
    r = requests.post(webhook_url, json=payload, timeout=10)
    r.raise_for_status()


# =========================
# Telegram
# =========================
def send_telegram_message(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    r.raise_for_status()


# =========================
# Google News RSS
# =========================
def get_google_news(keyword: str):
    encoded = quote_plus(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        feed = feedparser.parse(url)
        return feed.entries or []
    except Exception as exc:
        print(f"  - RSS 파싱 실패: {exc}")
        return []


def parse_entry_time(entry):
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime.fromtimestamp(timegm(entry.updated_parsed), tz=timezone.utc)

        published = entry.get("published") or entry.get("updated")
        if published:
            parsed = parsedate_to_datetime(published)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    return None


# =========================
# Unified Digest Formatter
# =========================
def format_digest(keyword: str, entries: list, for_telegram=False) -> str:
    """
    동일한 논리의 메시지
    - Slack: <url|title>
    - Telegram: <a href="url">title</a>
    """
    safe_keyword = html.escape(keyword, quote=False)
    lines = [f"🗞️ [뉴스 요약] {safe_keyword}", ""]

    kst = timezone(timedelta(hours=9))

    for idx, entry in enumerate(entries, start=1):
        raw_title = entry.get("title", "제목 없음")
        raw_link = entry.get("link", "")
        # HTML 특수문자만 이스케이프 (quote=False로 따옴표는 유지)
        title = html.escape(raw_title, quote=False)
        link = raw_link

        published_time = parse_entry_time(entry)
        if published_time:
            time_str = published_time.astimezone(kst).strftime("%H:%M")
        else:
            time_str = "시간 미상"

        if for_telegram:
            # URL은 이미 인코딩되어 있으므로 그대로 사용
            title_line = f'{idx}) <a href="{link}">{title}</a> | {time_str}'
        else:
            safe_title = title.replace("|", "¦").replace(">", "›")
            safe_link = link.replace(">", "›")
            title_line = f"{idx}) <{safe_link}|{safe_title}> | {time_str}"

        lines.append(title_line)
        lines.append("")

    return "\n".join(lines).rstrip()


def format_failure_alert(keyword: str, failures: list) -> str:
    safe_keyword = html.escape(keyword, quote=False)
    lines = [f"⚠️ 전송 실패 알림: {safe_keyword}", ""]
    for item in failures:
        lines.append(f"- {html.escape(item, quote=False)}")
    return "\n".join(lines).rstrip()


def normalize_keyword(value: str) -> str:
    normalized = value.strip().lower()
    synonym_map = {
        "naver": "네이버",
    }
    return synonym_map.get(normalized, normalized)


def load_sent_ids(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        sent_map = payload.get("sent", {})
        if isinstance(sent_map, dict):
            return sent_map
        sent_ids = payload.get("sent_ids", [])
        if isinstance(sent_ids, list):
            return {entry_id: None for entry_id in sent_ids}
    except Exception as exc:
        print(f"  - 전송 기록 읽기 실패: {exc}")
    return {}


def save_sent_ids(path: str, sent_map: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sent": sent_map}, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"  - 전송 기록 저장 실패: {exc}")


def get_entry_id(entry):
    raw_id = entry.get("id") or entry.get("guid") or entry.get("link")
    if raw_id:
        return raw_id
    title = entry.get("title", "")
    published = entry.get("published", "") or entry.get("updated", "")
    if title or published:
        return f"{title}|{published}"
    return None


# =========================
# 한국 공휴일 체크
# =========================
def is_korean_holiday(date: datetime, additional_holidays: List[str] = None) -> bool:
    """
    한국 공휴일 체크 (KST 기준)
    고정 공휴일 + 환경변수로 지정한 추가 공휴일 체크
    추가 공휴일은 "YYYY-MM-DD" 형식의 문자열 리스트
    """
    kst = timezone(timedelta(hours=9))
    kst_date = date.astimezone(kst)
    year = kst_date.year
    month = kst_date.month
    day = kst_date.day
    date_str = kst_date.strftime("%Y-%m-%d")
    
    # 고정 공휴일
    fixed_holidays = [
        (1, 1),   # 신정
        (3, 1),   # 삼일절
        (5, 5),   # 어린이날
        (6, 6),   # 현충일
        (8, 15),  # 광복절
        (10, 3),  # 개천절
        (10, 9),  # 한글날
        (12, 25), # 크리스마스
    ]
    
    if (month, day) in fixed_holidays:
        return True
    
    # 부처님오신날 (음력 4월 8일, 매년 다름)
    # 2024년: 5월 15일, 2025년: 5월 5일 등
    # 여기서는 간단하게 처리 (필요시 환경변수로 지정)
    buddha_birthdays = {
        2024: (5, 15),
        2025: (5, 5),
        2026: (5, 24),
    }
    if (month, day) == buddha_birthdays.get(year):
        return True
    
    # 환경변수로 지정한 추가 공휴일 체크 (설날, 추석 등)
    if additional_holidays and date_str in additional_holidays:
        return True
    
    return False


# =========================
# Slack 발송 가능 시간 체크
# =========================
def can_send_to_slack(now: datetime = None, additional_holidays: List[str] = None) -> bool:
    """
    Slack 발송 가능 여부 체크 (KST 기준)
    - 주말 (토요일, 일요일) 제한
    - 한국 공휴일 제한
    - 밤 8시 ~ 오전 6시 제한
    """
    kst = timezone(timedelta(hours=9))
    if now is None:
        now = datetime.now(timezone.utc)
    kst_now = now.astimezone(kst)
    
    # 주말 체크 (토요일=5, 일요일=6)
    weekday = kst_now.weekday()
    if weekday >= 5:  # 토요일 또는 일요일
        return False
    
    # 공휴일 체크
    if is_korean_holiday(kst_now, additional_holidays):
        return False
    
    # 시간대 체크 (20:00 ~ 06:00)
    hour = kst_now.hour
    if hour >= 20 or hour < 6:
        return False
    
    return True


# =========================
# Slack 메시지 큐 관리
# =========================
def load_slack_queue(path: str) -> List[Dict]:
    """슬랙 메시지 큐 로드"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("queue", [])
    except Exception as exc:
        print(f"  - 슬랙 큐 읽기 실패: {exc}")
        return []


def save_slack_queue(path: str, queue: List[Dict]):
    """슬랙 메시지 큐 저장"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"queue": queue}, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"  - 슬랙 큐 저장 실패: {exc}")


def add_to_slack_queue(path: str, keyword: str, message: str, entries_count: int, webhook_url: str):
    """슬랙 메시지 큐에 추가 (웹훅 URL 포함)"""
    queue = load_slack_queue(path)
    queue.append({
        "keyword": keyword,
        "message": message,
        "entries_count": entries_count,
        "webhook_url": webhook_url,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_slack_queue(path, queue)


def send_queued_slack_messages(queue_path: str) -> int:
    """큐에 있는 슬랙 메시지들을 모두 발송하고 큐 비우기 (각 메시지의 webhook_url 사용)"""
    queue = load_slack_queue(queue_path)
    if not queue:
        return 0
    
    sent_count = 0
    failed_items = []
    
    for item in queue:
        webhook_url = item.get("webhook_url")
        if not webhook_url:
            print(f"  ⚠️ 큐 메시지에 webhook_url이 없음: {item.get('keyword', '알 수 없음')}")
            continue
            
        try:
            send_slack_message(webhook_url, item["message"])
            sent_count += 1
            print(f"  ✅ 큐 메시지 발송: {item['keyword']} ({item['entries_count']}건)")
        except Exception as exc:
            print(f"  ❌ 큐 메시지 발송 실패 ({item['keyword']}): {exc}")
            # 실패한 항목은 다시 큐에 넣기
            failed_items.append(item)
    
    # 실패한 항목만 큐에 다시 저장 (발송 성공한 항목은 제거)
    if sent_count > 0 or failed_items:
        save_slack_queue(queue_path, failed_items)
        if sent_count > 0:
            print(f"  📦 큐에서 {sent_count}개 메시지 발송 완료")
        if failed_items:
            print(f"  ⚠️ {len(failed_items)}개 메시지 발송 실패 - 큐에 유지")
    
    return sent_count


# =========================
# Main
# =========================
def main():
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("CHAT_ID")
    
    # 여러 슬랙 웹훅 URL 지원 (쉼표로 구분)
    # SLACK_WEBHOOK_URLS가 있으면 우선 사용, 없으면 SLACK_WEBHOOK_URL 사용 (하위 호환성)
    slack_webhook_urls_str = os.environ.get("SLACK_WEBHOOK_URLS", "")
    slack_webhook_url_legacy = os.environ.get("SLACK_WEBHOOK_URL", "")
    
    if slack_webhook_urls_str:
        slack_webhook_urls = [url.strip() for url in slack_webhook_urls_str.split(",") if url.strip()]
    elif slack_webhook_url_legacy:
        slack_webhook_urls = [slack_webhook_url_legacy]
    else:
        slack_webhook_urls = []
    
    keywords_str = os.environ.get("KEYWORD", "")
    slack_keywords_str = os.environ.get("SLACK_KEYWORDS", "")
    sent_state_path = os.environ.get("SENT_STATE_PATH", ".sent_articles.json")
    slack_queue_path = os.environ.get("SLACK_QUEUE_PATH", ".slack_queue.json")
    retention_days = int(os.environ.get("SENT_RETENTION_DAYS", 7))
    # 추가 공휴일 (설날, 추석 등) - "YYYY-MM-DD" 형식, 쉼표로 구분
    # 예: "2024-02-09,2024-02-10,2024-02-11,2024-09-15,2024-09-16,2024-09-17"
    additional_holidays_str = os.environ.get("ADDITIONAL_HOLIDAYS", "")
    additional_holidays = [d.strip() for d in additional_holidays_str.split(",") if d.strip()] if additional_holidays_str else []

    if not telegram_token or not telegram_chat_id:
        raise RuntimeError("TELEGRAM_TOKEN / CHAT_ID 환경변수가 필요합니다.")

    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
    if not keywords:
        raise RuntimeError("KEYWORD 환경변수가 비어 있습니다.")

    slack_keywords = {
        normalize_keyword(k)
        for k in slack_keywords_str.split(",")
        if k.strip()
    }
    if not slack_keywords:
        # SLACK_KEYWORDS가 비어 있으면 전체 키워드로 전송(기존 동작 유지)
        slack_keywords = {normalize_keyword(k) for k in keywords}

    print(f"🔍 키워드: {keywords}")

    sent_map = load_sent_ids(sent_state_path)
    now_utc = datetime.now(timezone.utc)
    retention_cutoff = now_utc - timedelta(days=retention_days)
    pruned_map = {}
    for entry_id, ts in sent_map.items():
        if not ts:
            pruned_map[entry_id] = ts
            continue
        try:
            parsed = datetime.fromisoformat(ts)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed >= retention_cutoff:
                pruned_map[entry_id] = ts
        except Exception:
            pruned_map[entry_id] = ts
    sent_map = pruned_map

    for keyword in keywords:
        print(f"\n▶ 뉴스 수집: {keyword}")
        entries = get_google_news(keyword)

        if not entries:
            print("  - 뉴스 없음")
            continue

        # 최근 1시간 이내 + 중복 제거
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
        filtered_entries = []
        for entry in entries:
            published_time = parse_entry_time(entry)
            if not published_time:
                continue
            if published_time < cutoff_time:
                continue

            entry_id = get_entry_id(entry)
            if entry_id and entry_id in sent_map:
                continue

            filtered_entries.append(entry)

        if not filtered_entries:
            print("  - 최근 1시간 이내 새 기사 없음")
            continue

        # 상위 3건만 사용 (원하면 숫자 조절)
        MAX_ITEMS = int(os.environ.get("MAX_ITEMS", 3))
        entries = filtered_entries[:MAX_ITEMS]
        entry_ids = [get_entry_id(entry) for entry in entries]

        # Telegram
        tg_message = format_digest(keyword, entries, for_telegram=True)
        failures = []
        try:
            send_telegram_message(telegram_token, telegram_chat_id, tg_message)
            print(f"  ✅ Telegram 전송 ({len(entries)}건)")
            for entry_id in entry_ids:
                if entry_id and entry_id not in sent_map:
                    sent_map[entry_id] = datetime.now(timezone.utc).isoformat()
            save_sent_ids(sent_state_path, sent_map)
        except Exception as exc:
            print(f"  ❌ Telegram 전송 실패: {exc}")
            failures.append(f"Telegram 전송 실패: {exc}")

        # Slack (Webhook 없으면 스킵) - SLACK_KEYWORDS로 제한
        normalized_keyword = normalize_keyword(keyword)
        if slack_webhook_urls and normalized_keyword in slack_keywords:
            slack_message = format_digest(keyword, entries, for_telegram=False)
            
            # Slack 발송 가능 시간 체크
            if can_send_to_slack(additional_holidays=additional_holidays):
                # 모든 슬랙 채널에 발송
                for webhook_url in slack_webhook_urls:
                    try:
                        send_slack_message(webhook_url, slack_message)
                        print(f"  ✅ Slack 전송 ({len(entries)}건)")
                    except Exception as exc:
                        print(f"  ❌ Slack 전송 실패: {exc}")
                        failures.append(f"Slack 전송 실패: {exc}")
            else:
                # 발송 제한 시간이면 모든 채널에 대해 큐에 저장
                for webhook_url in slack_webhook_urls:
                    add_to_slack_queue(slack_queue_path, keyword, slack_message, len(entries), webhook_url)
                kst = timezone(timedelta(hours=9))
                kst_now = datetime.now(timezone.utc).astimezone(kst)
                print(f"  ⏸️ Slack 발송 제한 시간 - 큐에 저장 ({len(slack_webhook_urls)}개 채널, {len(entries)}건, 현재: {kst_now.strftime('%Y-%m-%d %H:%M KST')})")
        elif slack_webhook_urls:
            print(f"  ⏭️ Slack 전송 제외(키워드): {keyword}")

        if failures:
            alert_message = format_failure_alert(keyword, failures)
            try:
                send_telegram_message(telegram_token, telegram_chat_id, alert_message)
                print("  📣 실패 알림 전송 (Telegram)")
            except Exception as exc:
                print(f"  ❌ 실패 알림 전송 실패: {exc}")

    save_sent_ids(sent_state_path, sent_map)
    
    # Slack 큐에 있는 메시지들 발송 (발송 가능 시간이면)
    if slack_webhook_urls and can_send_to_slack(additional_holidays=additional_holidays):
        queued_count = send_queued_slack_messages(slack_queue_path)
        if queued_count > 0:
            print(f"\n📦 큐에 있던 {queued_count}개 Slack 메시지 발송 완료")
    
    print("\n🎉 모든 키워드 처리 완료")


if __name__ == "__main__":
    main()
