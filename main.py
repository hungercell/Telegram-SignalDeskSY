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
    r = requests.post(url, json=payload, timeout=10)
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
    safe_keyword = html.escape(keyword)
    lines = [f"🗞️ [뉴스 요약] {safe_keyword}", ""]

    kst = timezone(timedelta(hours=9))

    for idx, entry in enumerate(entries, start=1):
        raw_title = entry.get("title", "제목 없음")
        raw_link = entry.get("link", "")
        title = html.escape(raw_title)
        link = raw_link

        published_time = parse_entry_time(entry)
        if published_time:
            time_str = published_time.astimezone(kst).strftime("%H:%M")
        else:
            time_str = "시간 미상"

        if for_telegram:
            title_line = f'{idx}) <a href="{link}">{title}</a> | {time_str}'
        else:
            safe_title = title.replace("|", "¦").replace(">", "›")
            safe_link = link.replace(">", "›")
            title_line = f"{idx}) <{safe_link}|{safe_title}> | {time_str}"

        lines.append(title_line)
        lines.append("")

    return "\n".join(lines).rstrip()


def format_failure_alert(keyword: str, failures: list) -> str:
    safe_keyword = html.escape(keyword)
    lines = [f"⚠️ 전송 실패 알림: {safe_keyword}", ""]
    for item in failures:
        lines.append(f"- {html.escape(item)}")
    return "\n".join(lines).rstrip()


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
# Main
# =========================
def main():
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("CHAT_ID")
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    keywords_str = os.environ.get("KEYWORD", "")
    sent_state_path = os.environ.get("SENT_STATE_PATH", ".sent_articles.json")
    retention_days = int(os.environ.get("SENT_RETENTION_DAYS", 7))

    if not telegram_token or not telegram_chat_id:
        raise RuntimeError("TELEGRAM_TOKEN / CHAT_ID 환경변수가 필요합니다.")

    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
    if not keywords:
        raise RuntimeError("KEYWORD 환경변수가 비어 있습니다.")

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

        # Slack (Webhook 없으면 스킵)
        if slack_webhook_url:
            slack_message = format_digest(keyword, entries, for_telegram=False)
            try:
                send_slack_message(slack_webhook_url, slack_message)
                print(f"  ✅ Slack 전송 ({len(entries)}건)")
            except Exception as exc:
                print(f"  ❌ Slack 전송 실패: {exc}")
                failures.append(f"Slack 전송 실패: {exc}")

        if failures:
            alert_message = format_failure_alert(keyword, failures)
            try:
                send_telegram_message(telegram_token, telegram_chat_id, alert_message)
                print("  📣 실패 알림 전송 (Telegram)")
            except Exception as exc:
                print(f"  ❌ 실패 알림 전송 실패: {exc}")

    save_sent_ids(sent_state_path, sent_map)
    print("\n🎉 모든 키워드 처리 완료")


if __name__ == "__main__":
    main()
