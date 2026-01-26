"""
プロファイル設定管理モジュール
AdsPowerプロファイルと作品フォルダの紐付けを管理
"""
import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent
PROFILES_CONFIG_FILE = CONFIG_DIR / "profiles_config.json"


class ProfileConfig:
    """プロファイル設定データ"""

    def __init__(self, profile_id: str, profile_name: str, folders: list[str] = None):
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.folders = folders or []

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "folders": self.folders
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProfileConfig":
        return cls(
            profile_id=data.get("profile_id", ""),
            profile_name=data.get("profile_name", ""),
            folders=data.get("folders", [])
        )


class ProfileManager:
    """プロファイル設定マネージャー"""

    def __init__(self):
        self.api_url = "http://local.adspower.net:50325"
        self.selected_group_id = ""
        self.openai_api_key = ""
        self.profiles: list[ProfileConfig] = []
        self.load()

    def load(self):
        """設定ファイルから読み込み"""
        if PROFILES_CONFIG_FILE.exists():
            try:
                with open(PROFILES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.api_url = data.get("api_url", self.api_url)
                    self.selected_group_id = data.get("selected_group_id", "")
                    self.openai_api_key = data.get("openai_api_key", "")
                    self.profiles = [
                        ProfileConfig.from_dict(p)
                        for p in data.get("profiles", [])
                    ]
            except Exception as e:
                print(f"Config load error: {e}")

    def save(self):
        """設定ファイルに保存"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data = {
                "api_url": self.api_url,
                "selected_group_id": self.selected_group_id,
                "openai_api_key": self.openai_api_key,
                "profiles": [p.to_dict() for p in self.profiles]
            }
            with open(PROFILES_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")

    def add_profile(self, profile_id: str, profile_name: str) -> ProfileConfig:
        """プロファイルを追加"""
        # 既存チェック
        existing = self.get_profile(profile_id)
        if existing:
            return existing

        config = ProfileConfig(profile_id, profile_name)
        self.profiles.append(config)
        self.save()
        return config

    def remove_profile(self, profile_id: str):
        """プロファイルを削除"""
        self.profiles = [p for p in self.profiles if p.profile_id != profile_id]
        self.save()

    def get_profile(self, profile_id: str) -> Optional[ProfileConfig]:
        """プロファイルを取得"""
        for p in self.profiles:
            if p.profile_id == profile_id:
                return p
        return None

    def add_folder_to_profile(self, profile_id: str, folder_path: str):
        """プロファイルにフォルダを追加"""
        profile = self.get_profile(profile_id)
        if profile and folder_path not in profile.folders:
            profile.folders.append(folder_path)
            self.save()

    def remove_folder_from_profile(self, profile_id: str, folder_path: str):
        """プロファイルからフォルダを削除"""
        profile = self.get_profile(profile_id)
        if profile and folder_path in profile.folders:
            profile.folders.remove(folder_path)
            self.save()

    def update_profile_folders(self, profile_id: str, folders: list[str]):
        """プロファイルのフォルダリストを更新"""
        profile = self.get_profile(profile_id)
        if profile:
            profile.folders = folders
            self.save()
