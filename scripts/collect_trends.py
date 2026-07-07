# -*- coding: utf-8 -*-
"""
Totune 주간 동향 수집기
- Google News RSS + Apple 앱 랭킹 + Reddit RSS 수집
- Claude API로 "투튠에 의미 있는 것" 중심 요약
- 산출물: docs/trends/YYYY-Wxx.md (아카이브) + dashboard/trends.json (대시보드용)
"""
import json
import os
import datetime
import pathlib
import requests
import feedparser
from anthropic import Anthropic

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime.datetime.now()
WEEK_ID = NOW.strftime("%Y-W%V")

# ── 수집 소스 (전부 키 불필요) ─────────────────────────────
NEWS_QUERIES = [
    ("KR", "운세 앱", "ko"),
    ("KR", "사주 앱", "ko"),
    ("GLOBAL", "astrology app gen z", "en"),
    ("GLOBAL", "tarot app", "en"),
    ("GLOBAL", "korean fortune telling saju", "en"),
    ("TH", "แอปดูดวง", "th"),           # 태국어: 운세 앱
    ("BR", "aplicativo astrologia", "pt"),  # 포르투갈어: 점성술 앱
]

APP_RANKINGS = [
    ("KR", "https://rss.marketingtools.apple.com/api/v2/kr/apps/top-free/50/apps.json"),
    ("TH", "https://rss.marketingtools.apple.com/api/v2/th/apps/top-free/50/apps.json"),
    ("BR", "https://rss.marketingtools.apple.com/api/v2/br/apps/top-free/50/apps.json"),
]

REDDIT_FEEDS = [
    "https://www.reddit.com/r/astrology/top/.rss?t=week",
    "https://www.reddit.com/r/tarot/top/.rss?t=week",
]

FORTUNE_KEYWORDS = [
    "운세", "사주", "타로", "점신", "포스텔러", "astrolog", "tarot",
    "horoscope", "zodiac", "fortune", "ดวง", "astrologia", "tarô", "co-star", "costar",
]


def fetch_news():
    items = []
    for region, query, lang in NEWS_QUERIES:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl={lang}"
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:5]:
                items.append({
                    "region": region,
                    "query": query,
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "published": e.get("published", ""),
                })
        except Exception as ex:
            print(f"news fail {query}: {ex}")
    return items


def fetch_app_rankings():
    """라이프스타일 Top50 중 운세류 앱만 필터해 순위 추적."""
    results = []
    for region, url in APP_RANKINGS:
        try:
            data = requests.get(url, timeout=15).json()
            for i, app in enumerate(data.get("feed", {}).get("results", []), 1):
                name = (app.get("name", "") + " " + app.get("artistName", "")).lower()
                if any(k in name for k in FORTUNE_KEYWORDS):
                    results.append({"region": region, "rank": i, "name": app.get("name", "")})
        except Exception as ex:
            print(f"ranking fail {region}: {ex}")
    return results


def fetch_reddit():
    items = []
    headers = {"User-Agent": "totune-trends/1.0"}
    for url in REDDIT_FEEDS:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            feed = feedparser.parse(r.text)
            for e in feed.entries[:5]:
                items.append({"title": e.get("title", ""), "link": e.get("link", "")})
        except Exception as ex:
            print(f"reddit fail: {ex}")
    return items


def summarize(news, rankings, reddit):
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    raw = json.dumps({"news": news, "app_rankings": rankings, "reddit": reddit},
                     ensure_ascii=False, indent=1)[:14000]
    prompt = f"""너는 투튠(Totune)의 시장 동향 분석가다.
투튠: AI 타로+K사주 앱. 타겟은 한국 Z세대와 태국/브라질 해외 시장.
전략: Z세대로 확산(갑자 카드 공유, 최애 궁합), 밀레니얼로 수금(심층 분석, 대운 가이드).

아래 이번 주 수집 데이터를 분석해서 save_trends 툴로 저장하라.
주의: 데이터에 없는 내용을 지어내지 마라. 근거 약하면 insights에서 빼라.
인사이트 문장 안에서 큰따옴표(") 인용은 쓰지 마라.

수집 데이터:
{raw}"""

    trends_tool = {
        "name": "save_trends",
        "description": "주간 동향 요약을 저장한다",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "description": "이번 주 한 줄 요약, 한국어 40자 이내"},
                "insights": {"type": "array", "items": {"type": "string"},
                             "description": "투튠에 의미 있는 동향 3~5개, 각 한국어 1~2문장"},
                "competitor_moves": {"type": "array", "items": {"type": "string"},
                                     "description": "경쟁/유사 앱 움직임, 없으면 빈 배열"},
                "action": {"type": "string", "description": "이번 주 실행할 액션 딱 1개, 한국어 1문장"},
                "sources": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "link": {"type": "string"}},
                    "required": ["title", "link"]},
                    "description": "인사이트 근거 링크 최대 5개"},
            },
            "required": ["headline", "insights", "action", "sources"],
        },
    }

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        tools=[trends_tool],
        tool_choice={"type": "tool", "name": "save_trends"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            summary = dict(block.input)
            summary["week"] = WEEK_ID
            return summary
    raise RuntimeError("tool_use 블록이 응답에 없음 — API 응답 구조 확인 필요")


def write_outputs(summary, rankings):
    # 1) 대시보드용 JSON (최근 8주 유지)
    dash_path = ROOT / "dashboard" / "trends.json"
    history = []
    if dash_path.exists():
        try:
            history = json.loads(dash_path.read_text(encoding="utf-8")).get("history", [])
        except Exception:
            history = []
    history = [h for h in history if h.get("week") != WEEK_ID]
    entry = dict(summary)
    entry["rankings"] = rankings
    entry["updated"] = NOW.strftime("%Y-%m-%d %H:%M")
    history.insert(0, entry)
    dash_path.write_text(
        json.dumps({"history": history[:8]}, ensure_ascii=False, indent=1), encoding="utf-8")

    # 2) 아카이브 MD
    md_dir = ROOT / "docs" / "trends"
    md_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 주간 동향 — {WEEK_ID}",
        f"> 자동 생성: {entry['updated']}",
        "",
        f"## 📌 {summary.get('headline', '')}",
        "",
        "## 인사이트",
    ]
    lines += [f"- {i}" for i in summary.get("insights", [])]
    if summary.get("competitor_moves"):
        lines += ["", "## 경쟁 동향"] + [f"- {c}" for c in summary["competitor_moves"]]
    if rankings:
        lines += ["", "## 앱 랭킹 (라이프스타일 무료 Top50 내 운세류)"]
        lines += [f"- [{r['region']}] #{r['rank']} {r['name']}" for r in rankings]
    lines += ["", f"## ✅ 이번 주 액션", f"- {summary.get('action', '')}", "", "## 출처"]
    lines += [f"- [{s.get('title','')}]({s.get('link','')})" for s in summary.get("sources", [])]
    (md_dir / f"{WEEK_ID}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"written: trends.json + docs/trends/{WEEK_ID}.md")


def main():
    news = fetch_news()
    rankings = fetch_app_rankings()
    reddit = fetch_reddit()
    print(f"collected: news={len(news)} rankings={len(rankings)} reddit={len(reddit)}")
    if not (news or rankings or reddit):
        raise RuntimeError("모든 소스 수집 실패 — 네트워크 또는 피드 주소 확인 필요")
    summary = summarize(news, rankings, reddit)
    write_outputs(summary, rankings)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        print("=" * 50)
        print("[TRENDS FAIL] 아래 트레이스백을 확인:")
        traceback.print_exc()
        raise SystemExit(1)
