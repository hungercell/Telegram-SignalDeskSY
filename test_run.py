#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로컬 테스트: 포맷 검증 + (선택) 실제 RSS 수집
실행: python test_run.py
"""

import os
import sys

# 프로젝트 루트에서 main 모듈 로드
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_format():
    """요약 포맷(🗞️ [뉴스 요약] 키워드 + 1) 제목 - 출처 | HH:MM) 검증"""
    from main import format_digest, get_article_time_kst, get_article_source, KST

    # mock entries (feedparser 스타일)
    class MockEntry:
        def __init__(self, title, link, source_name="테스트뉴스", published_parsed=None):
            self._title = title
            self._link = link
            self._source = {"title": source_name} if source_name else None
            self._published_parsed = published_parsed or (2025, 2, 23, 8, 30, 0, 0, 54, 0)

        def get(self, key, default=None):
            if key == "title": return self._title
            if key == "link": return self._link
            if key == "source": return self._source
            if key == "id": return self._link
            if key == "published": return None
            if key == "updated": return None
            return default

        @property
        def published_parsed(self):
            return self._published_parsed

        @property
        def updated_parsed(self):
            return None

    entries = [
        MockEntry("첫 번째 뉴스 제목", "https://example.com/1", "뉴시스"),
        MockEntry("두 번째 뉴스 제목", "https://example.com/2", "연합뉴스"),
    ]

    tg_msg = format_digest("네이버", entries, for_telegram=True)
    slack_msg = format_digest("네이버", entries, for_telegram=False)

    assert "🗞️ [뉴스 요약] 네이버" in tg_msg, "Telegram 요약 헤더 없음"
    assert "1)" in tg_msg and "2)" in tg_msg, "번호 목록 없음"
    assert "뉴시스" in tg_msg and "연합뉴스" in tg_msg, "출처 없음"
    assert "첫 번째 뉴스 제목" in tg_msg and "https://example.com/1" in tg_msg

    assert "🗞️ [뉴스 요약] 네이버" in slack_msg, "Slack 요약 헤더 없음"
    assert "1)" in slack_msg and "2)" in slack_msg
    print("[OK] 요약 포맷 검증 통과")
    print("--- Telegram 예시 (앞 3줄) ---")
    print("\n".join(tg_msg.split("\n")[:3]))
    print("--- Slack 예시 (앞 3줄) ---")
    print("\n".join(slack_msg.split("\n")[:3]))
    return True


def test_main_dry():
    """실제 RSS 수집만 수행 (전송은 하지 않음). 전송용 env가 없으면 자동으로 스킵됨."""
    print("\n--- 실제 뉴스 수집 테스트 (전송 없음) ---")
    os.environ.setdefault("KEYWORD", "네이버,스테이블코인")
    os.environ.setdefault("SLACK_KEYWORDS", "네이버,스테이블코인")
    # 전송 방지: 없으면 main에서 전송 스킵
    if "TELEGRAM_TOKEN" not in os.environ:
        os.environ.pop("TELEGRAM_TOKEN", None)
        os.environ.pop("CHAT_ID", None)
    if "SLACK_WEBHOOK_URLS" not in os.environ:
        os.environ.pop("SLACK_WEBHOOK_URLS", None)
    # 테스트용 sent 파일 (기존 건드리지 않음)
    os.environ["SENT_STATE_PATH"] = ".sent_articles_test.json"

    from main import main
    main()
    print("\n(위에서 '새 뉴스 N건 요약 발송' 또는 '새 뉴스 없음' 확인)")


if __name__ == "__main__":
    try:
        test_format()
        test_main_dry()
        print("\n✅ 테스트 완료")
    except Exception as e:
        print(f"\n❌ 실패: {e}")
        raise
