#!/usr/bin/env python3
"""
monitor.py - 競合サイトのテキストを取得し、前回との差分を検出する
"""

import json
import os
import hashlib
import difflib
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config" / "targets.json"
RAW_DIR = BASE_DIR / "data" / "raw"
CHANGES_PATH = BASE_DIR / "data" / "changes.json"

RAW_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_text(url: str, selectors: dict, settings: dict) -> dict:
    """URLからメインテキストを取得し、タイトルと本文を返す"""
    headers = {"User-Agent": settings["user_agent"]}
    resp = requests.get(url, headers=headers, timeout=settings["request_timeout"])
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding

    soup = BeautifulSoup(resp.text, "html.parser")

    # タイトル取得
    title = ""
    for sel in selectors["title"].split(","):
        el = soup.select_one(sel.strip())
        if el:
            title = el.get_text(strip=True)
            break

    # メインコンテンツ取得（スクリプト・スタイル除去）
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    content = ""
    for sel in selectors["main_content"].split(","):
        el = soup.select_one(sel.strip())
        if el:
            content = el.get_text(separator="\n", strip=True)
            break

    max_len = settings["max_text_length"]
    return {
        "title": title[:200],
        "content": content[:max_len],
    }


def load_previous(target_id: str) -> dict | None:
    path = RAW_DIR / f"{target_id}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_snapshot(target_id: str, data: dict) -> None:
    path = RAW_DIR / f"{target_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_diff(old_text: str, new_text: str) -> list[str]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    return diff[:200]  # 差分が大きすぎる場合は先頭200行に制限


def run_monitoring() -> list[dict]:
    config = load_config()
    settings = config["settings"]
    now = datetime.now(timezone.utc).isoformat()
    change_records = []

    for target in config["targets"]:
        if not target.get("enabled", True):
            print(f"[SKIP] {target['name']}")
            continue

        print(f"[CHECK] {target['name']} ({target['url']})")
        try:
            fetched = fetch_text(target["url"], target["selectors"], settings)
        except Exception as e:
            print(f"  [ERROR] 取得失敗: {e}")
            change_records.append({
                "id": target["id"],
                "name": target["name"],
                "url": target["url"],
                "status": "error",
                "error": str(e),
                "checked_at": now,
            })
            continue

        new_hash = content_hash(fetched["content"])
        previous = load_previous(target["id"])

        snapshot = {
            "id": target["id"],
            "name": target["name"],
            "url": target["url"],
            "title": fetched["title"],
            "content": fetched["content"],
            "hash": new_hash,
            "fetched_at": now,
        }

        if previous is None:
            print("  [NEW] 初回取得")
            save_snapshot(target["id"], snapshot)
            change_records.append({**snapshot, "status": "new", "diff": []})
        elif previous["hash"] != new_hash:
            print("  [CHANGED] 変更を検出")
            diff = compute_diff(previous["content"], fetched["content"])
            save_snapshot(target["id"], snapshot)
            change_records.append({
                **snapshot,
                "status": "changed",
                "diff": diff,
                "previous_fetched_at": previous["fetched_at"],
            })
        else:
            print("  [NO CHANGE] 変更なし")
            change_records.append({
                "id": target["id"],
                "name": target["name"],
                "url": target["url"],
                "status": "unchanged",
                "checked_at": now,
                "diff": [],
            })

    return change_records


def main():
    print(f"=== 競合監視開始: {datetime.now(timezone.utc).isoformat()} ===")
    results = run_monitoring()

    # 結果をchanges.jsonに書き出す（analyze.pyが読み込む）
    with open(CHANGES_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    changed = [r for r in results if r["status"] == "changed"]
    new = [r for r in results if r["status"] == "new"]
    print(f"\n=== 完了: 変更あり {len(changed)} 件 / 初回取得 {len(new)} 件 ===")

    # 変更があった場合は exit code 0、なければ 0（Actions側でフィルタ）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
