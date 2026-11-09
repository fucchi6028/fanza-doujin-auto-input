"""
プロファイル設定管理モジュール
AdsPowerプロファイルと作品フォルダの紐付けを管理
"""
import json
import os
import re
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
        self.api_url = "http://127.0.0.1:50325"
        self.selected_group_id = ""
        self.openai_api_key = ""
        self.profiles: list[ProfileConfig] = []
        self.load()

    def _extract_profile_number(self, profile: ProfileConfig) -> int:
        """プロファイル名から数字を抽出してソート用のキーを返す"""
        match = re.search(r'-(\d+)$', profile.profile_name)
        if match:
            return int(match.group(1))
        return 999999  # 数字がない場合は末尾に配置

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
                    # プロファイル名の数字順にソート
                    self.profiles.sort(key=self._extract_profile_number)
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

    def reconcile_ids(self, live_profiles: list[dict]) -> list[tuple[str, str, str]]:
        """AdsPower実機のプロファイル一覧と突合し、名前一致で古いprofile_idを現行IDに更新する。

        AdsPower側でプロファイルを作り直すと user_id が変わり、ローカル保存のIDが
        「Profile does not exist」になる。フォルダ紐付けは保持したままIDだけ張り替える。
        戻り値: 更新した (profile_name, old_id, new_id) のリスト。
        """
        live_ids = {p.get("user_id") for p in live_profiles}
        # 名前 -> 現行user_id（同名が複数ある場合は最初のものを採用）
        name_to_id: dict[str, str] = {}
        for p in live_profiles:
            name = p.get("name")
            if name and name not in name_to_id:
                name_to_id[name] = p.get("user_id")

        changes: list[tuple[str, str, str]] = []
        for local in self.profiles:
            if local.profile_id in live_ids:
                continue  # 現行IDなのでOK
            new_id = name_to_id.get(local.profile_name)
            if new_id and new_id != local.profile_id:
                changes.append((local.profile_name, local.profile_id, new_id))
                local.profile_id = new_id
        if changes:
            self.save()
        return changes

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

    def add_folders_to_profile(self, profile_id: str, folder_paths: list[str]) -> int:
        """プロファイルに複数フォルダをまとめて追加（重複は除外）。追加件数を返す"""
        profile = self.get_profile(profile_id)
        if not profile:
            return 0
        added = 0
        for folder_path in folder_paths:
            if folder_path not in profile.folders:
                profile.folders.append(folder_path)
                added += 1
        if added:
            self.save()
        return added

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
