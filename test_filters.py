#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from main import get_article_exclusion_reason


class ArticleFilterTests(unittest.TestCase):
    @staticmethod
    def entry(title, source="테스트뉴스"):
        return {
            "title": title,
            "source": {"title": source},
            "link": "https://example.com/article",
        }

    def test_blocks_platea_magazine_source(self):
        reason = get_article_exclusion_reason(
            self.entry("정상적으로 보이는 기사", " Platea Magazine ")
        )
        self.assertIn("차단 출처", reason)

    def test_blocks_platea_magazine_in_title_when_source_is_missing(self):
        reason = get_article_exclusion_reason(
            self.entry("넥슨 관련 소식 - Platea Magazine", "")
        )
        self.assertIn("Platea Magazine", reason)

    def test_blocks_investment_promotion_titles(self):
        titles = [
            "증권가, 삼성전자 목표주가 상향",
            "지금이 매수 기회? 저평가 종목 분석",
            "상승 여력 40% 남은 유망주",
            "3 Stocks to Buy Right Now",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNotNone(
                    get_article_exclusion_reason(self.entry(title))
                )

    def test_keeps_normal_company_and_industry_news(self):
        titles = [
            "삼성전자, 차세대 반도체 생산 확대",
            "넥슨 신작 출시 후 주가 상승",
            "네이버, AI 검색 서비스 개편",
            "스테이블코인 제도화 논의 본격화",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNone(
                    get_article_exclusion_reason(self.entry(title))
                )


if __name__ == "__main__":
    unittest.main()
