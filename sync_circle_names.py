"""config/circle_names.json のサークル名を管理DB（accounts.name）へ反映する。

extract_circle_names.py / _extract_096_097.py で取得した結果を
出品管理表（SQLite）に同期する。取得失敗・エラーのエントリはスキップ。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

from management import db

BASE = Path(__file__).parent
JSON_PATH = BASE / "config" / "circle_names.json"
DB_PATH = BASE / "management" / "management.db"


def main():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    updated, skipped = [], []
    for code, d in data.items():
        name = (d.get("circle_name") or "").strip()
        if not name or "失敗" in name or "エラー" in name:
            skipped.append(code)
            continue
        db.set_account_name(code, name, str(DB_PATH))
        updated.append((code, name))

    print(f"反映: {len(updated)}件 / スキップ: {len(skipped)}件")
    for code, name in updated:
        print(f"  {code}: {name}")
    if skipped:
        print(f"  スキップ(未取得/失敗): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
