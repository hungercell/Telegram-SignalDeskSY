#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
구글 뉴스 RSS를 가져와 텔레그램으로 보내는 봇
GitHub Actions에서 실행되며, 최근 15분 이내 발행된 뉴스만 전송합니다.
"""

import os
import feedparser
import requests
from datetime import datetime, timedelta
from time import mktime

def get_google_news_rss(keyword):
    """
    구글 뉴스 RSS 피드를 가져옵니다.
    
    Args:
        keyword: 검색할 키워드
        
    Returns:
        feedparser 객체
    """
    # 구글 뉴스 RSS URL (한국어 설정)
    url = f"https://news.google.com/rss/search?q={keyword}&hl=ko&gl=KR&ceid=KR:ko"
    
    try:
        feed = feedparser.parse(url)
        return feed
    except Exception as e:
        print(f"RSS 피드 가져오기 실패: {e}")
        return None

def filter_recent_news(feed, minutes=15):
    """
    최근 N분 이내에 발행된 뉴스만 필터링합니다.
    
    Args:
        feed: feedparser 객체
        minutes: 필터링할 시간(분), 기본값 15분
        
    Returns:
        최근 뉴스 항목 리스트
    """
    if not feed or not feed.entries:
        return []
    
    # 현재 시간
    now = datetime.now()
    # 기준 시간 (현재 시간 - N분)
    cutoff_time = now - timedelta(minutes=minutes)
    
    recent_news = []
    
    for entry in feed.entries:
        try:
            # RSS 항목의 발행 시간 파싱
            # published_parsed는 struct_time 객체
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                # struct_time을 datetime으로 변환
                published_time = datetime.fromtimestamp(mktime(entry.published_parsed))
                
                # 최근 15분 이내인지 확인
                if published_time >= cutoff_time:
                    recent_news.append(entry)
        except Exception as e:
            # 시간 파싱 실패 시 해당 항목은 건너뜀
            print(f"뉴스 항목 시간 파싱 실패: {e}")
            continue
    
    return recent_news

def send_telegram_message(token, chat_id, message):
    """
    텔레그램으로 메시지를 전송합니다.
    
    Args:
        token: 텔레그램 봇 토큰
        chat_id: 채팅 ID
        message: 전송할 메시지
        
    Returns:
        성공 여부 (bool)
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"텔레그램 메시지 전송 실패: {e}")
        return False

def format_news_message(entry):
    """
    뉴스 항목을 텔레그램 메시지 형식으로 포맷팅합니다.
    
    Args:
        entry: feedparser 항목 객체
        
    Returns:
        포맷팅된 메시지 문자열
    """
    title = entry.get('title', '제목 없음')
    link = entry.get('link', '')
    published = entry.get('published', '')
    
    # published_parsed가 있으면 한국 시간으로 포맷팅
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            published_time = datetime.fromtimestamp(mktime(entry.published_parsed))
            published = published_time.strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass
    
    message = f"<b>{title}</b>\n\n"
    message += f"발행 시간: {published}\n"
    message += f"링크: {link}"
    
    return message

def main():
    """
    메인 실행 함수
    """
    # 환경변수에서 설정값 가져오기
    telegram_token = os.environ.get('TELEGRAM_TOKEN')
    telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    keyword = os.environ.get('NEWS_KEYWORD', 'Discord')  # 기본값: Discord
    
    # 필수 환경변수 확인
    if not telegram_token:
        print("오류: TELEGRAM_TOKEN 환경변수가 설정되지 않았습니다.")
        return
    
    if not telegram_chat_id:
        print("오류: TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        return
    
    print(f"키워드 '{keyword}'로 뉴스를 검색합니다...")
    
    # 구글 뉴스 RSS 가져오기
    feed = get_google_news_rss(keyword)
    
    if not feed:
        print("RSS 피드를 가져올 수 없습니다.")
        return
    
    # 최근 15분 이내 뉴스 필터링
    recent_news = filter_recent_news(feed, minutes=15)
    
    if not recent_news:
        print("최근 15분 이내 발행된 뉴스가 없습니다.")
        return
    
    print(f"최근 15분 이내 뉴스 {len(recent_news)}개를 찾았습니다.")
    
    # 각 뉴스를 텔레그램으로 전송
    success_count = 0
    for entry in recent_news:
        message = format_news_message(entry)
        
        if send_telegram_message(telegram_token, telegram_chat_id, message):
            success_count += 1
            print(f"뉴스 전송 성공: {entry.get('title', '제목 없음')}")
        else:
            print(f"뉴스 전송 실패: {entry.get('title', '제목 없음')}")
    
    print(f"총 {success_count}/{len(recent_news)}개 뉴스 전송 완료.")

if __name__ == "__main__":
    main()

