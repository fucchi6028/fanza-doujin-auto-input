"""
シリーズ別設定保存モジュール
ベースフォルダ名ごとにキーワードと販売情報を保存
"""
import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent
SERIES_STORAGE_FILE = CONFIG_DIR / "series_storage.json"


class SeriesStorage:
    """シリーズ別設定保存マネージャー"""

    def __init__(self):
        self.storage: dict[str, dict] = {}
        self.load()

    def load(self):
        """設定ファイルから読み込み"""
        if SERIES_STORAGE_FILE.exists():
            try:
                with open(SERIES_STORAGE_FILE, "r", encoding="utf-8") as f:
                    self.storage = json.load(f)
            except Exception as e:
                print(f"Series storage load error: {e}")
                self.storage = {}
        else:
            self.storage = {}

    def save(self):
        """設定ファイルに保存"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(SERIES_STORAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.storage, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Series storage save error: {e}")

    def _get_or_create(self, base_folder_name: str) -> dict:
        """ベースフォルダのデータを取得（なければ作成）"""
        if base_folder_name not in self.storage:
            self.storage[base_folder_name] = {
                "keywords": [],
                "sales_info": {}
            }
        return self.storage[base_folder_name]

    # キーワード関連
    def get_keywords(self, base_folder_name: str) -> list[str]:
        """キーワードを取得"""
        data = self.storage.get(base_folder_name, {})
        return data.get("keywords", [])

    def save_keywords(self, base_folder_name: str, keywords: list[str]):
        """キーワードを保存"""
        data = self._get_or_create(base_folder_name)
        data["keywords"] = keywords
        self.save()

    # 販売情報関連
    def get_sales_info(self, base_folder_name: str) -> dict:
        """販売情報を取得"""
        data = self.storage.get(base_folder_name, {})
        return data.get("sales_info", {})

    def save_sales_info(self, base_folder_name: str, sales_info: dict):
        """販売情報を保存"""
        data = self._get_or_create(base_folder_name)
        data["sales_info"] = sales_info
        self.save()

    def has_data(self, base_folder_name: str) -> bool:
        """指定したベースフォルダにデータが保存されているか"""
        return base_folder_name in self.storage

    def delete_data(self, base_folder_name: str):
        """ベースフォルダのデータを削除"""
        if base_folder_name in self.storage:
            del self.storage[base_folder_name]
            self.save()


# 後方互換性のためのエイリアス
KeywordStorage = SeriesStorage
