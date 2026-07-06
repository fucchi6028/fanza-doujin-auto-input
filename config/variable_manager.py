"""
変数設定管理モジュール
説明文テンプレート内の変数を管理
"""
import json
import os
import zipfile
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent
VARIABLES_CONFIG_FILE = CONFIG_DIR / "variables_config.json"

# 画像ファイルの拡張子
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


class VariableConfig:
    """変数設定データ"""

    def __init__(self, name: str, var_type: str = "static", value: str = "",
                 min_images: int = 50, description: str = ""):
        """
        Args:
            name: 変数名（[[変数名]]の形式で使用）
            var_type: 変数タイプ
                - "static": 固定値
                - "series": シリーズ名から取得
                - "product": 商品名から取得
                - "character": 画像枚数でキャラクターフォルダを検出
            value: 固定値の場合の値
            min_images: characterタイプの場合の最小画像枚数
            description: 説明
        """
        self.name = name
        self.var_type = var_type
        self.value = value
        self.min_images = min_images
        self.description = description

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "var_type": self.var_type,
            "value": self.value,
            "min_images": self.min_images,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VariableConfig":
        return cls(
            name=data.get("name", ""),
            var_type=data.get("var_type", "static"),
            value=data.get("value", ""),
            min_images=data.get("min_images", 50),
            description=data.get("description", ""),
        )


class VariableManager:
    """変数設定マネージャー"""

    def __init__(self):
        self.variables: list[VariableConfig] = []
        self.load()

    def load(self):
        """設定ファイルから読み込み"""
        if VARIABLES_CONFIG_FILE.exists():
            try:
                with open(VARIABLES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.variables = [
                        VariableConfig.from_dict(v)
                        for v in data.get("variables", [])
                    ]
            except Exception as e:
                print(f"Variable config load error: {e}")
                self._set_defaults()
        else:
            self._set_defaults()

    def _set_defaults(self):
        """デフォルト変数を設定"""
        self.variables = [
            VariableConfig(
                name="シリーズ名",
                var_type="series",
                description="シリーズフォルダ名"
            ),
            VariableConfig(
                name="商品フォルダ",
                var_type="product",
                description="商品フォルダ名"
            ),
            VariableConfig(
                name="キャラクター1",
                var_type="character",
                min_images=50,
                description="画像50枚以上のサブフォルダ1"
            ),
            VariableConfig(
                name="キャラクター2",
                var_type="character",
                min_images=50,
                description="画像50枚以上のサブフォルダ2"
            ),
            VariableConfig(
                name="キャラクター3",
                var_type="character",
                min_images=50,
                description="画像50枚以上のサブフォルダ3"
            ),
        ]
        self.save()

    def save(self):
        """設定ファイルに保存"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data = {
                "variables": [v.to_dict() for v in self.variables]
            }
            with open(VARIABLES_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Variable config save error: {e}")

    def add_variable(self, var: VariableConfig):
        """変数を追加"""
        # 重複チェック
        if not self.get_variable(var.name):
            self.variables.append(var)
            self.save()

    def remove_variable(self, name: str):
        """変数を削除"""
        self.variables = [v for v in self.variables if v.name != name]
        self.save()

    def get_variable(self, name: str) -> Optional[VariableConfig]:
        """変数を取得"""
        for v in self.variables:
            if v.name == name:
                return v
        return None

    def update_variable(self, name: str, **kwargs):
        """変数を更新"""
        var = self.get_variable(name)
        if var:
            for key, value in kwargs.items():
                if hasattr(var, key):
                    setattr(var, key, value)
            self.save()


def count_images_in_folder(folder_path: Path, recursive: bool = False) -> int:
    """フォルダ内の画像ファイル数をカウント

    Args:
        folder_path: 対象フォルダのパス
        recursive: Trueの場合、サブフォルダ内の画像も再帰的にカウント
    """
    count = 0
    try:
        for f in folder_path.iterdir():
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                count += 1
            elif recursive and f.is_dir():
                # サブフォルダ（構図フォルダ等）内の画像も再帰的にカウント
                count += count_images_in_folder(f, recursive=True)
    except Exception:
        pass
    return count


def get_character_folders(product_path: Path, min_images: int = 50) -> list[str]:
    """
    商品フォルダ内から画像が指定枚数以上あるサブフォルダを取得

    フォルダ構造:
        商品フォルダ ⇒ キャラクター ⇒ 構図 ⇒ 画像
    構図フォルダ内の画像も再帰的にカウントし、キャラクターフォルダを正しく検出

    Returns:
        フォルダ名のリスト（画像枚数の多い順）
    """
    folders = []
    try:
        for subfolder in product_path.iterdir():
            if subfolder.is_dir():
                # 構図フォルダ内の画像も再帰的にカウント
                image_count = count_images_in_folder(subfolder, recursive=True)
                if image_count >= min_images:
                    folders.append((subfolder.name, image_count))

        # 画像枚数の多い順にソート
        folders.sort(key=lambda x: x[1], reverse=True)
        return [f[0] for f in folders]
    except Exception as e:
        print(f"Get character folders error: {e}")
        return []


def count_total_images_in_character_folders(product_path: Path, min_images: int = 50) -> int:
    """
    商品フォルダ内の画像が指定枚数以上あるサブフォルダの画像合計数を取得

    フォルダ構造:
        商品フォルダ ⇒ キャラクター ⇒ 構図 ⇒ 画像
    構図フォルダ内の画像も再帰的にカウント

    Args:
        product_path: 商品フォルダのパス
        min_images: 最小画像枚数（これ以上のフォルダのみカウント）

    Returns:
        画像の合計枚数
    """
    total = 0
    try:
        for subfolder in product_path.iterdir():
            if subfolder.is_dir():
                # 構図フォルダ内の画像も再帰的にカウント
                image_count = count_images_in_folder(subfolder, recursive=True)
                if image_count >= min_images:
                    total += image_count
    except Exception as e:
        print(f"Count total images error: {e}")
    return total


def count_all_images_in_product(product_path: Path) -> int:
    """
    商品フォルダ内のすべての画像をカウント（直下＋サブフォルダ、再帰的）

    フォルダ構造:
        商品フォルダ ⇒ キャラクター ⇒ 構図 ⇒ 画像
    構図フォルダ内の画像も含めて再帰的にカウント

    Args:
        product_path: 商品フォルダのパス

    Returns:
        画像の合計枚数
    """
    # 再帰的にすべての画像をカウント
    return count_images_in_folder(product_path, recursive=True)


def count_images_in_zip_folder(product_path: Path) -> int:
    """
    商品フォルダ内の「商品フォルダ.zip」フォルダから画像をカウント

    Args:
        product_path: 商品フォルダのパス

    Returns:
        画像の合計枚数
    """
    zip_folder = product_path / "商品フォルダ.zip"
    if zip_folder.exists() and zip_folder.is_dir():
        return count_images_in_folder(zip_folder)
    return 0


def count_images_in_masked_zip(product_path: Path, masked_title: str = "") -> int:
    """商品フォルダ内の「タイトルの2文字目が伏字（〇/○）になったZIP」から画像枚数を取得する。

    完成品はタイトルの2文字目を伏字にしたZIPで納品されるため、配信申請ページの
    「枚数」にはこのZIP内の画像点数を使う。ZIPを解凍せず中身の一覧だけを数える。

    Args:
        product_path: 商品フォルダのパス
        masked_title: 伏字済みタイトル（例:「シリーズ名 A〇C」）。一致するZIPを優先する。

    Returns:
        ZIP内の画像枚数（該当ZIPが見つからなければ0）
    """
    try:
        zip_files = [
            f for f in product_path.iterdir()
            if f.is_file() and f.suffix.lower() == ".zip"
        ]
    except Exception as e:
        print(f"Count masked zip error: {e}")
        return 0

    if not zip_files:
        return 0

    def _count_in_zip(zip_path: Path) -> int:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                return sum(
                    1 for name in zf.namelist()
                    if not name.endswith("/")
                    and Path(name).suffix.lower() in IMAGE_EXTENSIONS
                )
        except Exception as e:
            print(f"Read zip error ({zip_path.name}): {e}")
            return 0

    # 1. 伏字タイトルと完全一致するZIPを優先
    if masked_title:
        for zf in zip_files:
            if zf.stem == masked_title:
                return _count_in_zip(zf)

    # 2. 名前に伏字文字（〇 U+3007 / ○ U+25CB）を含むZIPを優先
    masked_zips = [zf for zf in zip_files if "〇" in zf.name or "○" in zf.name]
    if masked_zips:
        return _count_in_zip(masked_zips[0])

    # 3. ZIPが1つだけならそれを枚数元とみなす
    if len(zip_files) == 1:
        return _count_in_zip(zip_files[0])

    return 0


def mask_second_char(text: str) -> str:
    """2文字目を〇で伏字にする"""
    if len(text) >= 2:
        return text[0] + "〇" + text[2:]
    return text


def process_description_template(
    template: str,
    series_name: str,
    product_path: Path,
    variable_manager: VariableManager
) -> str:
    """
    説明文テンプレートの変数を置換

    Args:
        template: テンプレート文字列
        series_name: シリーズ名
        product_path: 商品フォルダのパス
        variable_manager: 変数マネージャー

    Returns:
        置換後の文字列
    """
    result = template

    # キャラクターフォルダを取得（重複なし）
    character_folders = []
    min_images_list = [v.min_images for v in variable_manager.variables if v.var_type == "character"]
    min_images = min(min_images_list) if min_images_list else 50
    all_character_folders = get_character_folders(product_path, min_images)

    # キャラクター変数のインデックス
    char_index = 0

    for var in variable_manager.variables:
        placeholder = f"[[{var.name}]]"

        if placeholder not in result:
            continue

        replacement = ""

        if var.var_type == "series":
            # シリーズ名（伏字なし）
            replacement = series_name

        elif var.var_type == "product":
            # 商品名（伏字）
            replacement = mask_second_char(product_path.name)

        elif var.var_type == "character":
            # キャラクターフォルダ（重複なし、伏字）
            if char_index < len(all_character_folders):
                replacement = mask_second_char(all_character_folders[char_index])
                char_index += 1
            else:
                replacement = ""

        elif var.var_type == "static":
            # 固定値（伏字）
            replacement = mask_second_char(var.value) if var.value else ""

        result = result.replace(placeholder, replacement)

    return result


def read_description_file(product_path: Path) -> str:
    """
    商品フォルダから説明文ファイルを読み込み

    Args:
        product_path: 商品フォルダのパス

    Returns:
        説明文の内容（見つからない場合は空文字）
    """
    # 可能なファイル名（スペース付きも対応）
    possible_names = ["説明文.txt", "説明文 .txt", "説明文", "description.txt"]

    for name in possible_names:
        file_path = product_path / name
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"Read description file error: {e}")

    return ""
