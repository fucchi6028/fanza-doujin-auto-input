"""
シリーズ別設定保存モジュール
プロファイル＋シリーズ名ごとにキーワードと販売情報を保存
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

    @staticmethod
    def make_key(profile_id: str, series_name: str) -> str:
        """プロファイルIDとシリーズ名からストレージキーを生成"""
        return f"{profile_id}_{series_name}"

    def _get_or_create(self, key: str) -> dict:
        """データを取得（なければ作成）"""
        if key not in self.storage:
            self.storage[key] = {
                "keywords": [],
                "sales_info": {}
            }
        return self.storage[key]

    # キーワード関連
    def get_keywords(self, key: str) -> list[str]:
        """キーワードを取得"""
        data = self.storage.get(key, {})
        return data.get("keywords", [])

    def get_keywords_by_profile_series(self, profile_id: str, series_name: str) -> list[str]:
        """プロファイルIDとシリーズ名でキーワードを取得"""
        key = self.make_key(profile_id, series_name)
        return self.get_keywords(key)

    def save_keywords(self, key: str, keywords: list[str]):
        """キーワードを保存"""
        data = self._get_or_create(key)
        data["keywords"] = keywords
        self.save()

    def save_keywords_by_profile_series(self, profile_id: str, series_name: str, keywords: list[str]):
        """プロファイルIDとシリーズ名でキーワードを保存"""
        key = self.make_key(profile_id, series_name)
        self.save_keywords(key, keywords)

    # 販売情報関連
    def get_sales_info(self, key: str) -> dict:
        """販売情報を取得"""
        data = self.storage.get(key, {})
        return data.get("sales_info", {})

    def get_sales_info_by_profile_series(self, profile_id: str, series_name: str) -> dict:
        """プロファイルIDとシリーズ名で販売情報を取得"""
        key = self.make_key(profile_id, series_name)
        return self.get_sales_info(key)

    def save_sales_info(self, key: str, sales_info: dict):
        """販売情報を保存"""
        data = self._get_or_create(key)
        data["sales_info"] = sales_info
        self.save()

    def save_sales_info_by_profile_series(self, profile_id: str, series_name: str, sales_info: dict):
        """プロファイルIDとシリーズ名で販売情報を保存"""
        key = self.make_key(profile_id, series_name)
        self.save_sales_info(key, sales_info)

    def has_data(self, key: str) -> bool:
        """指定したキーにデータが保存されているか"""
        return key in self.storage

    def delete_data(self, key: str):
        """データを削除"""
        if key in self.storage:
            del self.storage[key]
            self.save()


# 後方互換性のためのエイリアス
KeywordStorage = SeriesStorage
