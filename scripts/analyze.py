#!/usr/bin/env python3
"""
analyze.py - 変更検出された差分をGemini APIで戦略分析し、レポートJSONを生成する
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from groq import Groq

BASE_DIR = Path(__file__).parent.parent
CHANGES_PATH = BASE_DIR / "data" / "changes.json"
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"
REPORT_PATH = BASE_DIR / "data" / "report.json"
HISTORY_PATH = BASE_DIR / "data" / "history.json"

ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_TEMPLATE = """あなたは優秀な競合インテリジェンスアナリストです。
競合他社のウェブサイトの変更内容を差分から詳細に読み取り、以下の観点で分析してください。

差分の読み方: 「+」で始まる行が追加、「-」で始まる行が削除です。

回答は必ず以下のJSON形式のみで返してください（説明文・コードブロック不要）:
{{
  "change_details": [
    "追加・削除・変更された具体的な内容を箇条書きで（例: 新コース「3Days集中講座」を追加、キャンペーン終了日を4/12→4/26に変更）",
    "もう一つの具体的な変更点"
  ],
  "change_nature": "変更カテゴリ（新商品/価格改定/キャンペーン/メッセージング変更/コンテンツ刷新 など）",
  "strategic_intent": "なぜこの変更を行ったと考えられるか（2〜3文で具体的に）",
  "business_impact": "自社への影響（2〜3文で具体的に）",
  "recommended_actions": {{
    "short_term": ["今週〜今月中にすべき具体的なアクション"],
    "mid_term": ["1〜3ヶ月以内にすべき具体的なアクション"]
  }},
  "threat_level": 3,
  "confidence": "high",
  "summary": "今回の変更全体を3〜5文で詳しく要約。何が変わり、なぜ重要で、自社としてどう動くべきかを含める"
}}

---
競合他社: {name}
URL: {url}
検出日時: {checked_at}
ページタイトル: {title}

【変更差分（unified diff形式）】
{diff}

【現在のページ内容（抜粋）】
{content}"""


def build_prompt(record: dict) -> str:
    diff_text = "\n".join(record.get("diff", []))[:3000]
    return PROMPT_TEMPLATE.format(
        name=record["name"],
        url=record["url"],
        checked_at=record.get("fetched_at", record.get("checked_at", "")),
        title=record.get("title", "不明"),
        diff=diff_text if diff_text else "(初回取得のため差分なし - 現在のコンテンツを分析してください)",
        content=record.get("content", "")[:2000],
    )


def analyze_with_groq(record: dict, client) -> dict:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": build_prompt(record)}],
        temperature=0.3,
    )
    raw_text = response.choices[0].message.content.strip()

    if "```json" in raw_text:
        raw_text = raw_text.split("```json")[1].split("```")[0].strip()
    elif "```" in raw_text:
        raw_text = raw_text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "change_nature": "解析エラー",
            "strategic_intent": "不明",
            "business_impact": "不明",
            "recommended_actions": {"short_term": [], "mid_term": []},
            "threat_level": 0,
            "confidence": "low",
            "summary": raw_text[:200],
        }


def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("[ERROR] GROQ_API_KEY が設定されていません")
        return 1

    if not CHANGES_PATH.exists():
        print("[ERROR] changes.json が見つかりません。monitor.py を先に実行してください")
        return 1

    with open(CHANGES_PATH, encoding="utf-8") as f:
        changes = json.load(f)

    targets = [r for r in changes if r["status"] in ("changed", "new")]
    print(f"=== Gemini分析開始: {len(targets)} 件 ===")

    client = Groq(api_key=api_key)

    now = datetime.now(timezone.utc).isoformat()
    analyses = []

    for record in targets:
        print(f"[ANALYZE] {record['name']}")
        try:
            result = analyze_with_groq(record, client)
            analysis_record = {
                "id": record["id"],
                "name": record["name"],
                "url": record["url"],
                "status": record["status"],
                "analyzed_at": now,
                "analysis": result,
            }
            analyses.append(analysis_record)

            out_path = ANALYSIS_DIR / f"{record['id']}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(analysis_record, f, ensure_ascii=False, indent=2)

            print(f"  脅威レベル: {result.get('threat_level', '?')} / 信頼度: {result.get('confidence', '?')}")
        except Exception as e:
            print(f"  [ERROR] 分析失敗: {e}")

    analysis_map = {a["id"]: a for a in analyses}

    report = {
        "generated_at": now,
        "summary": {
            "total": len(changes),
            "changed": len([r for r in changes if r["status"] == "changed"]),
            "new": len([r for r in changes if r["status"] == "new"]),
            "unchanged": len([r for r in changes if r["status"] == "unchanged"]),
            "error": len([r for r in changes if r["status"] == "error"]),
        },
        "results": [],
    }

    for record in changes:
        entry = {
            "id": record["id"],
            "name": record["name"],
            "url": record["url"],
            "status": record["status"],
            "checked_at": record.get("fetched_at") or record.get("checked_at", ""),
        }
        if record["id"] in analysis_map:
            entry["analysis"] = analysis_map[record["id"]]["analysis"]
        report["results"].append(entry)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 履歴に追記
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = []
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)
    history = [h for h in history if h.get("date") != today]
    history.insert(0, {"date": today, **report})
    history = history[:90]  # 直近90日分を保持
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了: report.json・history.json を生成しました ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
