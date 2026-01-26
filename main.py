import flet as ft
import json
import os
import sys
import time
import threading
from pathlib import Path

# パス設定
sys.path.insert(0, str(Path(__file__).parent))

from browser.adspower import AdsPowerAPI, AdsPowerBrowser
from config.profile_manager import ProfileManager, ProfileConfig
from config.variable_manager import (
    VariableManager,
    VariableConfig,
    process_description_template,
    read_description_file,
    count_total_images_in_character_folders,
    get_character_folders,
)
from config.keywords_data import (
    KEYWORD_CATEGORIES,
    POPULAR_KEYWORDS,
    KEYWORDS_BY_CATEGORY,
    get_keyword_name,
)
from config.keyword_storage import KeywordStorage
from utils.furigana import FuriganaConverter

# 定数定義
ARTICLE_TYPES = [
    ("comic", "コミック・小説"),
    ("cg", "CG (イラスト・動画)"),
    ("game", "ゲーム"),
    ("voice", "ボイス・音楽"),
]

AI_GENERATED_TYPES = [
    ("1", "AIを利用していない"),
    ("2", "AIで作品を生成している"),
    ("3", "AIを一部利用して作品を生成している"),
    ("4", "AIを作品生成に補助的に利用している"),
]

SECTIONS = [
    ("1", "男性向け"),
    ("2", "BL"),
    ("3", "TL/乙女向け"),
]

KEYWORD_AGES = [
    ("156023", "成人向け"),
    ("23", "全年齢向け"),
]

PARODY_TYPES = [
    ("1", "オリジナル"),
    ("2", "漫画・アニメパロディ"),
    ("3", "ゲームパロディ"),
    ("4", "パロディその他"),
]

VR_OPTIONS = [
    ("", "なし"),
    ("156012", "VR対応"),
    ("156011", "VR専用"),
]

DRM_OPTIONS = [
    ("none", "なし"),
    ("protect", "プロテクトサービス"),
    ("sdrm", "ソーシャルDRM (β版)"),
]

CAMPAIGN_AUTO_JOIN_OPTIONS = [
    ("0", "発売からすぐに自動参加する"),
    ("30", "発売から30日後に自動参加する"),
    ("90", "発売から90日後に自動参加する"),
    ("null", "自動参加しない"),
]



def mask_second_char(text: str) -> str:
    """2文字目を〇で伏字にする"""
    if len(text) >= 2:
        return text[0] + "〇" + text[2:]
    return text


def get_subfolders(folder_path: str) -> list[str]:
    """フォルダ内のサブフォルダ一覧を取得"""
    try:
        path = Path(folder_path)
        if path.exists() and path.is_dir():
            return [f.name for f in path.iterdir() if f.is_dir()]
    except Exception as e:
        print(f"Folder read error: {e}")
    return []


class ProductData:
    """作品データを管理するクラス"""

    def __init__(self):
        self.data = self._get_default_data()

    def _get_default_data(self):
        return {
            "title": "",
            "title_ruby": "",
            "article_type": "cg",
            "ai_generated_type": "2",
            "section": "1",
            "keyword_age": "156023",
            "comment": "",
            "file_number": "",
            "keywords": [],
            "parody_type": "",
            "parody_names": ["", "", "", ""],
            "keyword_event": "0",
            "vr_compatible": "",
            "price_retail": "",
            "monopoly_hope_flg": "0",
            "drm_hope": "none",
            "revision_flg": "1",
            "campaign_kibou_flg": True,
            "is_coupon_usable": True,
            "campaign_auto_join_flg_set_days": "0",
            "release_date_type": "1",
            "release_date": "",
            "note": "",
            "files": {
                "main": "",
                "package": "",
                "thumbnail": "",
                "samples": []
            }
        }

    def load_from_file(self, filepath: str):
        with open(filepath, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    def save_to_file(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)


def main(page: ft.Page):
    page.title = "FANZA同人 自動入力ツール"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 1200
    page.window.height = 800

    # 状態管理
    product = ProductData()
    profile_manager = ProfileManager()
    variable_manager = VariableManager()
    keyword_storage = KeywordStorage()
    ads_api = AdsPowerAPI(profile_manager.api_url)
    furigana_converter = FuriganaConverter(profile_manager.openai_api_key)

    # 現在選択中の状態
    current_profile_id = None
    current_base_folder = None
    current_series_folder = None
    current_product_folder = None

    # ========== 共通コンポーネント ==========
    status_text = ft.Text("準備完了", size=12)

    def show_status(message: str):
        status_text.value = message
        page.update()

    def show_snackbar(message: str, color=None):
        page.snack_bar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=color
        )
        page.snack_bar.open = True
        page.update()

    # ========== 設定タブ ==========
    def create_settings_tab():
        # API URL
        api_url_field = ft.TextField(
            label="AdsPower API URL",
            value=profile_manager.api_url,
            width=400,
        )

        # OpenAI API Key
        openai_api_key_field = ft.TextField(
            label="OpenAI API Key",
            value=profile_manager.openai_api_key,
            width=400,
            password=True,
            can_reveal_password=True,
        )

        # OpenAI接続状態
        openai_status = ft.Text("未確認", color=ft.colors.GREY)

        def test_openai(e):
            api_key = openai_api_key_field.value.strip()
            if not api_key:
                openai_status.value = "APIキーが空です"
                openai_status.color = ft.colors.ORANGE
                page.update()
                return

            furigana_converter.set_api_key(api_key)
            success, result = furigana_converter.convert("テスト")
            if success:
                openai_status.value = "接続OK"
                openai_status.color = ft.colors.GREEN
                profile_manager.openai_api_key = api_key
                profile_manager.save()
                show_snackbar("OpenAI APIに接続しました", ft.colors.GREEN_700)
            else:
                openai_status.value = f"接続失敗: {result}"
                openai_status.color = ft.colors.RED
                show_snackbar(f"接続失敗: {result}", ft.colors.RED_700)
            page.update()

        # 接続状態
        connection_status = ft.Text("未接続", color=ft.colors.GREY)

        def test_connection(e):
            ads_api.api_url = api_url_field.value
            connected, msg = ads_api.check_connection()
            if connected:
                connection_status.value = "接続OK"
                connection_status.color = ft.colors.GREEN
                profile_manager.api_url = api_url_field.value
                profile_manager.save()
                show_snackbar("AdsPowerに接続しました", ft.colors.GREEN_700)
                load_groups()
            else:
                connection_status.value = f"接続失敗: {msg}"
                connection_status.color = ft.colors.RED
                show_snackbar(f"接続失敗: {msg}", ft.colors.RED_700)
            page.update()

        # グループ選択
        group_dropdown = ft.Dropdown(
            label="グループ選択",
            width=300,
            on_change=lambda e: load_profiles_for_group(e.control.value)
        )

        # プロファイルリスト（AdsPowerから取得）
        available_profiles_list = ft.ListView(
            expand=True,
            spacing=5,
            height=200,
        )

        # 登録済みプロファイルリスト
        registered_profiles_list = ft.ListView(
            expand=True,
            spacing=5,
            height=300,
        )

        # 選択中プロファイルのフォルダリスト
        selected_profile_for_folders = {"id": None, "name": None}
        folders_list = ft.ListView(
            expand=True,
            spacing=5,
            height=200,
        )
        folders_section_title = ft.Text("フォルダ設定", size=16, weight=ft.FontWeight.BOLD)

        def load_groups():
            groups = ads_api.get_groups()
            group_dropdown.options = [
                ft.dropdown.Option(key=g.get("group_id"), text=g.get("group_name"))
                for g in groups
            ]
            if profile_manager.selected_group_id:
                group_dropdown.value = profile_manager.selected_group_id
            page.update()

        def load_profiles_for_group(group_id: str):
            if not group_id:
                return
            profile_manager.selected_group_id = group_id
            profile_manager.save()

            profiles = ads_api.get_profiles(group_id)
            available_profiles_list.controls = [
                ft.ListTile(
                    leading=ft.Icon(ft.icons.PERSON),
                    title=ft.Text(p.get("name", "Unknown")),
                    subtitle=ft.Text(p.get("user_id", ""), size=10),
                    trailing=ft.IconButton(
                        ft.icons.ADD,
                        on_click=lambda e, pid=p.get("user_id"), pname=p.get("name"): add_profile(pid, pname)
                    ),
                )
                for p in profiles
            ]
            page.update()

        def add_profile(profile_id: str, profile_name: str):
            profile_manager.add_profile(profile_id, profile_name)
            refresh_registered_profiles()
            show_snackbar(f"プロファイル追加: {profile_name}")

        def remove_profile(profile_id: str):
            profile_manager.remove_profile(profile_id)
            refresh_registered_profiles()
            show_snackbar("プロファイルを削除しました")

        def select_profile_for_folders(profile_id: str, profile_name: str):
            selected_profile_for_folders["id"] = profile_id
            selected_profile_for_folders["name"] = profile_name
            folders_section_title.value = f"フォルダ設定: {profile_name}"
            refresh_folders_list()

        def refresh_folders_list():
            profile_id = selected_profile_for_folders["id"]
            if not profile_id:
                folders_list.controls = []
                page.update()
                return

            profile = profile_manager.get_profile(profile_id)
            if not profile:
                folders_list.controls = []
                page.update()
                return

            folders_list.controls = [
                ft.ListTile(
                    leading=ft.Icon(ft.icons.FOLDER),
                    title=ft.Text(Path(folder).name, size=14),
                    subtitle=ft.Text(folder, size=10),
                    trailing=ft.IconButton(
                        ft.icons.DELETE,
                        on_click=lambda e, f=folder: remove_folder(f)
                    ),
                )
                for folder in profile.folders
            ]
            page.update()

        def add_folder_dialog(e):
            profile_id = selected_profile_for_folders["id"]
            if not profile_id:
                show_snackbar("先にプロファイルを選択してください", ft.colors.ORANGE_700)
                return

            def on_result(result: ft.FilePickerResultEvent):
                if result.path:
                    profile_manager.add_folder_to_profile(profile_id, result.path)
                    refresh_folders_list()
                    show_snackbar(f"フォルダ追加: {result.path}")

            file_picker = ft.FilePicker(on_result=on_result)
            page.overlay.append(file_picker)
            page.update()
            file_picker.get_directory_path(dialog_title="作品フォルダを選択")

        def remove_folder(folder_path: str):
            profile_id = selected_profile_for_folders["id"]
            if profile_id:
                profile_manager.remove_folder_from_profile(profile_id, folder_path)
                refresh_folders_list()

        def refresh_registered_profiles():
            registered_profiles_list.controls = []
            for p in profile_manager.profiles:
                card = ft.Card(
                    content=ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.icons.ACCOUNT_CIRCLE),
                            ft.Column([
                                ft.Text(p.profile_name, weight=ft.FontWeight.BOLD),
                                ft.Text(f"ID: {p.profile_id} | フォルダ: {len(p.folders)}件", size=10),
                            ], spacing=2, expand=True),
                            ft.IconButton(
                                ft.icons.FOLDER_OPEN,
                                tooltip="フォルダ設定",
                                on_click=lambda e, pid=p.profile_id, pname=p.profile_name: select_profile_for_folders(pid, pname)
                            ),
                            ft.IconButton(
                                ft.icons.DELETE,
                                tooltip="削除",
                                on_click=lambda e, pid=p.profile_id: remove_profile(pid)
                            ),
                        ], spacing=10),
                        padding=10,
                    )
                )
                registered_profiles_list.controls.append(card)
            page.update()

        def save_settings(e):
            profile_manager.api_url = api_url_field.value
            profile_manager.openai_api_key = openai_api_key_field.value.strip()
            profile_manager.save()
            furigana_converter.set_api_key(profile_manager.openai_api_key)
            show_snackbar("設定を保存しました", ft.colors.GREEN_700)

        # 初期読み込み
        def init_settings():
            connected, _ = ads_api.check_connection()
            if connected:
                connection_status.value = "接続OK"
                connection_status.color = ft.colors.GREEN
                load_groups()
                if profile_manager.selected_group_id:
                    load_profiles_for_group(profile_manager.selected_group_id)

            # OpenAI APIキー確認
            if profile_manager.openai_api_key:
                openai_status.value = "APIキー設定済"
                openai_status.color = ft.colors.BLUE

            refresh_registered_profiles()
            refresh_variables_list()

        # ========== 変数設定 ==========
        variables_list = ft.ListView(
            expand=True,
            spacing=5,
            height=250,
        )

        # 変数タイプの選択肢
        VAR_TYPES = [
            ("series", "シリーズ名"),
            ("product", "商品名"),
            ("character", "キャラクター（画像数検出）"),
            ("static", "固定値"),
        ]

        def refresh_variables_list():
            variables_list.controls = []
            for var in variable_manager.variables:
                type_label = next((v[1] for v in VAR_TYPES if v[0] == var.var_type), var.var_type)
                detail_text = ""
                if var.var_type == "character":
                    detail_text = f"（{var.min_images}枚以上）"
                elif var.var_type == "static" and var.value:
                    detail_text = f"（値: {var.value}）"

                card = ft.Card(
                    content=ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.icons.CODE, size=20),
                            ft.Column([
                                ft.Text(f"[[{var.name}]]", weight=ft.FontWeight.BOLD),
                                ft.Text(f"{type_label}{detail_text}", size=11, color=ft.colors.GREY),
                            ], spacing=2, expand=True),
                            ft.IconButton(
                                ft.icons.DELETE,
                                tooltip="削除",
                                on_click=lambda e, n=var.name: delete_variable(n)
                            ),
                        ], spacing=10),
                        padding=10,
                    )
                )
                variables_list.controls.append(card)
            page.update()

        def delete_variable(name: str):
            variable_manager.remove_variable(name)
            refresh_variables_list()
            show_snackbar(f"変数を削除しました: {name}")

        # 新規変数追加用フィールド
        new_var_name = ft.TextField(label="変数名", width=150, hint_text="例: キャラクター4")
        new_var_type = ft.Dropdown(
            label="タイプ",
            width=200,
            options=[ft.dropdown.Option(key=k, text=v) for k, v in VAR_TYPES],
            value="character",
        )
        new_var_min_images = ft.TextField(
            label="最小画像数",
            width=100,
            value="50",
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        new_var_value = ft.TextField(label="固定値", width=150, visible=False)

        def on_var_type_change(e):
            var_type = new_var_type.value
            new_var_min_images.visible = (var_type == "character")
            new_var_value.visible = (var_type == "static")
            page.update()

        new_var_type.on_change = on_var_type_change

        def add_new_variable(e):
            name = new_var_name.value.strip()
            if not name:
                show_snackbar("変数名を入力してください", ft.colors.ORANGE_700)
                return

            if variable_manager.get_variable(name):
                show_snackbar("同じ名前の変数が既に存在します", ft.colors.ORANGE_700)
                return

            var_type = new_var_type.value
            min_images = 50
            value = ""

            if var_type == "character":
                try:
                    min_images = int(new_var_min_images.value)
                except ValueError:
                    min_images = 50

            if var_type == "static":
                value = new_var_value.value

            new_var = VariableConfig(
                name=name,
                var_type=var_type,
                min_images=min_images,
                value=value,
            )
            variable_manager.add_variable(new_var)
            refresh_variables_list()

            # フィールドをリセット
            new_var_name.value = ""
            new_var_value.value = ""
            show_snackbar(f"変数を追加しました: [[{name}]]", ft.colors.GREEN_700)
            page.update()

        # レイアウト
        settings_content = ft.Column([
            # API接続設定
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text("接続設定", size=18, weight=ft.FontWeight.BOLD),
                        ft.ElevatedButton(
                            "設定を保存",
                            icon=ft.icons.SAVE,
                            on_click=save_settings,
                            style=ft.ButtonStyle(bgcolor=ft.colors.BLUE_700)
                        ),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(),
                    ft.Text("AdsPower API", size=14, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        api_url_field,
                        ft.ElevatedButton("接続テスト", on_click=test_connection),
                        connection_status,
                    ], spacing=10),
                    ft.Divider(height=20),
                    ft.Text("OpenAI API (ふりがな生成用)", size=14, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        openai_api_key_field,
                        ft.ElevatedButton("接続テスト", on_click=test_openai),
                        openai_status,
                    ], spacing=10),
                ]),
                padding=15,
                border=ft.border.all(1, ft.colors.OUTLINE),
                border_radius=10,
                margin=ft.margin.only(bottom=10),
            ),

            # グループ・プロファイル選択
            ft.Row([
                # 左: AdsPowerプロファイル
                ft.Container(
                    content=ft.Column([
                        ft.Text("AdsPowerプロファイル", size=16, weight=ft.FontWeight.BOLD),
                        group_dropdown,
                        ft.Text("プロファイル一覧:", size=12),
                        available_profiles_list,
                    ]),
                    expand=1,
                    padding=15,
                    border=ft.border.all(1, ft.colors.OUTLINE),
                    border_radius=10,
                ),

                # 中央: 登録済みプロファイル
                ft.Container(
                    content=ft.Column([
                        ft.Text("登録済みプロファイル", size=16, weight=ft.FontWeight.BOLD),
                        ft.Text("※フォルダアイコンでフォルダ設定", size=10, color=ft.colors.GREY),
                        registered_profiles_list,
                    ]),
                    expand=1,
                    padding=15,
                    border=ft.border.all(1, ft.colors.OUTLINE),
                    border_radius=10,
                ),

                # 右: フォルダ設定
                ft.Container(
                    content=ft.Column([
                        folders_section_title,
                        ft.ElevatedButton(
                            "フォルダ追加",
                            icon=ft.icons.CREATE_NEW_FOLDER,
                            on_click=add_folder_dialog
                        ),
                        folders_list,
                    ]),
                    expand=1,
                    padding=15,
                    border=ft.border.all(1, ft.colors.OUTLINE),
                    border_radius=10,
                ),
            ], spacing=10),

            # 変数設定
            ft.Container(
                content=ft.Column([
                    ft.Text("説明文変数設定", size=18, weight=ft.FontWeight.BOLD),
                    ft.Text("説明文.txt内で [[変数名]] の形式で使用。置換時は2文字目が〇に伏字されます。", size=11, color=ft.colors.GREY),
                    ft.Divider(),
                    ft.Row([
                        new_var_name,
                        new_var_type,
                        new_var_min_images,
                        new_var_value,
                        ft.ElevatedButton("追加", icon=ft.icons.ADD, on_click=add_new_variable),
                    ], spacing=10, wrap=True),
                    ft.Divider(),
                    ft.Text("登録済み変数:", size=12),
                    variables_list,
                ]),
                padding=15,
                border=ft.border.all(1, ft.colors.OUTLINE),
                border_radius=10,
                margin=ft.margin.only(top=10),
            ),
        ], scroll=ft.ScrollMode.AUTO)

        # 初期化実行
        init_settings()

        return settings_content

    # ========== 入力タブ ==========
    def create_input_tab():
        nonlocal current_profile_id, current_base_folder, current_series_folder, current_product_folder

        # プロファイル選択ドロップダウン
        profile_dropdown = ft.Dropdown(
            label="使用プロファイル",
            width=250,
            options=[
                ft.dropdown.Option(key=p.profile_id, text=p.profile_name)
                for p in profile_manager.profiles
            ]
        )

        # ベースフォルダ選択
        base_folder_dropdown = ft.Dropdown(
            label="ベースフォルダ",
            width=300,
        )

        # シリーズフォルダ選択
        series_dropdown = ft.Dropdown(
            label="シリーズ",
            width=250,
        )

        # 商品フォルダ選択
        product_dropdown = ft.Dropdown(
            label="商品フォルダ",
            width=250,
        )

        def on_profile_change(e):
            nonlocal current_profile_id
            current_profile_id = e.control.value
            profile = profile_manager.get_profile(current_profile_id)
            if profile:
                base_folder_dropdown.options = [
                    ft.dropdown.Option(key=f, text=Path(f).name)
                    for f in profile.folders
                ]
            else:
                base_folder_dropdown.options = []
            base_folder_dropdown.value = None
            series_dropdown.options = []
            series_dropdown.value = None
            product_dropdown.options = []
            product_dropdown.value = None
            page.update()

        def on_base_folder_change(e):
            nonlocal current_base_folder
            current_base_folder = e.control.value
            if current_base_folder:
                subfolders = get_subfolders(current_base_folder)
                series_dropdown.options = [
                    ft.dropdown.Option(key=f, text=f)
                    for f in sorted(subfolders)
                ]
                # 保存済み設定を読み込み
                base_name = Path(current_base_folder).name
                loaded_items = []

                # キーワード読み込み
                saved_keywords = keyword_storage.get_keywords(base_name)
                if saved_keywords:
                    selected_keywords.clear()
                    selected_keywords.extend(saved_keywords)
                    update_keyword_chips()
                    loaded_items.append("キーワード")

                # 販売情報読み込み
                sales_info = keyword_storage.get_sales_info(base_name)
                if sales_info:
                    price_field.value = sales_info.get("price_retail", "")
                    if price_field.value:
                        price_field.border_color = None
                        price_field.error_text = None
                    monopoly_switch.value = sales_info.get("monopoly_hope_flg", "0") == "1"
                    drm_dropdown.value = sales_info.get("drm_hope", "none")
                    campaign_checkbox.value = sales_info.get("campaign_kibou_flg", True)
                    coupon_checkbox.value = sales_info.get("is_coupon_usable", True)
                    auto_join_dropdown.value = sales_info.get("campaign_auto_join_flg_set_days", "0")
                    loaded_items.append("販売情報")

                if loaded_items:
                    show_snackbar(f"読み込み完了: {', '.join(loaded_items)} ({base_name})", ft.colors.BLUE_700)
            else:
                series_dropdown.options = []
            series_dropdown.value = None
            product_dropdown.options = []
            product_dropdown.value = None
            page.update()

        def on_series_change(e):
            nonlocal current_series_folder
            current_series_folder = e.control.value
            if current_base_folder and current_series_folder:
                series_path = Path(current_base_folder) / current_series_folder
                subfolders = get_subfolders(str(series_path))
                product_dropdown.options = [
                    ft.dropdown.Option(key=f, text=f)
                    for f in sorted(subfolders)
                ]
            else:
                product_dropdown.options = []
            product_dropdown.value = None
            page.update()

        def load_product_data():
            """商品フォルダからデータを読み込み（再読み込み対応）"""
            nonlocal current_product_folder
            if not (current_base_folder and current_series_folder and current_product_folder):
                return

            # タイトル自動生成（2文字目伏字）
            masked_product_name = mask_second_char(current_product_folder)
            generated_title = f"{current_series_folder} {masked_product_name}"
            title_field.value = generated_title

            # info.json読み込み
            product_path = Path(current_base_folder) / current_series_folder / current_product_folder
            info_path = product_path / "info.json"
            if info_path.exists():
                product.load_from_file(str(info_path))
                load_data_to_ui()
                show_status(f"読み込み完了: {product_path}")
            else:
                # info.jsonがない場合はタイトルだけセット
                product.data = product._get_default_data()
                product.data["title"] = generated_title
                load_data_to_ui()
                show_status(f"新規作品: {product_path}")

            # 説明文ファイルを読み込み、変数を置換
            description_template = read_description_file(product_path)
            if description_template:
                processed_description = process_description_template(
                    description_template,
                    current_series_folder,
                    product_path,
                    variable_manager
                )
                comment_field.value = processed_description
                product.data["comment"] = processed_description

            # 画像枚数を自動計算（50枚以上のフォルダのみ）
            total_images = count_total_images_in_character_folders(product_path, min_images=50)
            if total_images >= 500:
                file_number_field.value = str(total_images)
                file_number_field.error_text = "500枚以上は登録できません"
                show_snackbar(f"エラー: 画像が{total_images}枚あります（上限500枚）", ft.colors.RED_700)
            else:
                file_number_field.value = str(total_images) if total_images > 0 else ""
                file_number_field.error_text = None
            product.data["file_number"] = str(total_images) if total_images > 0 else ""

            # パロディ詳細を自動入力（伏字なしの元名称）
            # 詳細1: 作品フォルダ名
            parody_name_fields[0].value = current_product_folder
            # 詳細2-4: キャラクターフォルダ（50枚以上）
            character_folders = get_character_folders(product_path, min_images=50)
            for i in range(3):
                if i < len(character_folders):
                    parody_name_fields[i + 1].value = character_folders[i]
                else:
                    parody_name_fields[i + 1].value = ""

            # パロディ選択をリセット（手動設定を促す）
            parody_type_dropdown.value = ""
            parody_type_dropdown.border_color = ft.colors.RED
            parody_type_dropdown.error_text = "パロディを選択してください"

            # ふりがな自動生成（伏字なしのタイトルから）
            original_title = f"{current_series_folder} {current_product_folder}"
            generate_furigana_async(original_title)
            page.update()

        def on_product_change(e):
            nonlocal current_product_folder
            current_product_folder = e.control.value
            load_product_data()

        def reload_product_data(e):
            """商品フォルダデータを再読み込み"""
            if current_product_folder:
                load_product_data()
                show_snackbar("データを再読み込みしました", ft.colors.BLUE_700)

        profile_dropdown.on_change = on_profile_change
        base_folder_dropdown.on_change = on_base_folder_change
        series_dropdown.on_change = on_series_change
        product_dropdown.on_change = on_product_change

        # 基本設定フィールド
        title_field = ft.TextField(label="作品タイトル", hint_text="最大255文字", max_length=255, expand=True)
        title_ruby_field = ft.TextField(label="ふりがな", hint_text="ひらがなで入力", expand=True)

        def generate_furigana_async(title: str):
            """タイトルからふりがなを自動生成"""
            if not title or not profile_manager.openai_api_key:
                return

            # 伏字（〇）を除去してふりがな変換
            title_for_conversion = title.replace("〇", "")
            success, result = furigana_converter.convert(title_for_conversion)

            if success:
                title_ruby_field.value = result
                page.update()
        article_type_dropdown = ft.Dropdown(
            label="作品形式",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in ARTICLE_TYPES],
            value="cg", width=250,
        )
        ai_type_dropdown = ft.Dropdown(
            label="AI利用の有無",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in AI_GENERATED_TYPES],
            value="2", width=350,
        )
        section_dropdown = ft.Dropdown(
            label="作品区分",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in SECTIONS],
            value="1", width=200,
        )
        keyword_age_dropdown = ft.Dropdown(
            label="年齢指定",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in KEYWORD_AGES],
            value="156023", width=200,
        )
        comment_field = ft.TextField(label="作品内容（説明文）", multiline=True, min_lines=5, max_lines=10, expand=True)
        file_number_field = ft.TextField(label="ページ・枚数", hint_text="自動計算", width=150, read_only=True)
        # パロディ設定（未設定時は赤枠表示）
        parody_type_dropdown = ft.Dropdown(
            label="パロディ ※要設定",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in PARODY_TYPES],
            value="",
            width=250,
            border_color=ft.colors.RED,
            error_text="パロディを選択してください",
        )

        def on_parody_type_change(e):
            if parody_type_dropdown.value:
                parody_type_dropdown.border_color = None
                parody_type_dropdown.error_text = None
            else:
                parody_type_dropdown.border_color = ft.colors.RED
                parody_type_dropdown.error_text = "パロディを選択してください"
            page.update()

        parody_type_dropdown.on_change = on_parody_type_change

        parody_name_fields = [
            ft.TextField(label="パロディ詳細1（作品名）", width=200, read_only=True),
            ft.TextField(label="パロディ詳細2", width=200, read_only=True),
            ft.TextField(label="パロディ詳細3", width=200, read_only=True),
            ft.TextField(label="パロディ詳細4", width=200, read_only=True),
        ]
        vr_dropdown = ft.Dropdown(
            label="VR対応",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in VR_OPTIONS],
            value="", width=200,
        )

        # キーワード
        selected_keywords = []
        keyword_chips = ft.Row(wrap=True, spacing=5)
        keyword_count_text = ft.Text("選択中: 0/10", size=12)

        def update_keyword_chips():
            keyword_chips.controls = [
                ft.Chip(
                    label=ft.Text(get_keyword_name(kw)),
                    on_delete=lambda e, k=kw: remove_keyword(k),
                )
                for kw in selected_keywords
            ]
            keyword_count_text.value = f"選択中: {len(selected_keywords)}/10"
            if len(selected_keywords) >= 10:
                keyword_count_text.color = ft.colors.ORANGE
            else:
                keyword_count_text.color = None
            page.update()

        def add_keyword(keyword_id: str):
            if keyword_id not in selected_keywords and len(selected_keywords) < 10:
                selected_keywords.append(keyword_id)
                update_keyword_chips()

        def remove_keyword(keyword_id: str):
            if keyword_id in selected_keywords:
                selected_keywords.remove(keyword_id)
                update_keyword_chips()

        def save_keywords_to_storage():
            """現在のキーワードをベースフォルダに保存"""
            if current_base_folder:
                base_name = Path(current_base_folder).name
                keyword_storage.save_keywords(base_name, selected_keywords.copy())
                show_snackbar(f"キーワードを保存しました: {base_name}", ft.colors.GREEN_700)

        def load_keywords_from_storage():
            """ベースフォルダからキーワードを読み込み"""
            if current_base_folder:
                base_name = Path(current_base_folder).name
                saved_keywords = keyword_storage.get_keywords(base_name)
                if saved_keywords:
                    selected_keywords.clear()
                    selected_keywords.extend(saved_keywords)
                    update_keyword_chips()
                    return True
            return False

        # 人気キーワードボタン
        keyword_buttons = ft.Row(
            wrap=True, spacing=5,
            controls=[
                ft.ElevatedButton(
                    text=v,
                    on_click=lambda e, k=k: add_keyword(k),
                    style=ft.ButtonStyle(padding=5)
                )
                for k, v in POPULAR_KEYWORDS
            ]
        )

        # カテゴリ別キーワードタブ
        def create_category_keyword_buttons(category_id: str):
            keywords = KEYWORDS_BY_CATEGORY.get(category_id, [])
            return ft.Column(
                controls=[
                    ft.Row(
                        wrap=True,
                        spacing=3,
                        controls=[
                            ft.ElevatedButton(
                                text=name + (" ★" if is_featured else ""),
                                on_click=lambda e, k=kid: add_keyword(k),
                                style=ft.ButtonStyle(
                                    padding=5,
                                    bgcolor=ft.colors.AMBER_700 if is_featured else None,
                                )
                            )
                            for kid, name, is_featured in keywords
                        ],
                    )
                ],
                scroll=ft.ScrollMode.AUTO,
                height=180,
            )

        category_tabs = ft.Tabs(
            selected_index=0,
            tabs=[
                ft.Tab(
                    text=cat_name,
                    content=ft.Container(
                        content=create_category_keyword_buttons(cat_id),
                        padding=5,
                    ),
                )
                for cat_id, cat_name in KEYWORD_CATEGORIES
            ],
            height=220,
        )

        # 販売情報
        price_field = ft.TextField(
            label="販売価格（税抜）※要設定",
            hint_text="100円単位",
            suffix_text="円",
            width=200,
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=ft.colors.RED,
            error_text="価格を入力してください",
        )

        def on_price_change(e):
            if price_field.value and price_field.value.strip():
                price_field.border_color = None
                price_field.error_text = None
            else:
                price_field.border_color = ft.colors.RED
                price_field.error_text = "価格を入力してください"
            page.update()

        price_field.on_change = on_price_change

        monopoly_switch = ft.Switch(label="専売希望", value=False)
        drm_dropdown = ft.Dropdown(label="作品保護", options=[ft.dropdown.Option(key=k, text=v) for k, v in DRM_OPTIONS], value="none", width=250)
        campaign_checkbox = ft.Checkbox(label="FANZA負担キャンペーン参加", value=True)
        coupon_checkbox = ft.Checkbox(label="FANZA負担クーポン参加", value=True)
        auto_join_dropdown = ft.Dropdown(
            label="キャンペーン自動参加",
            options=[ft.dropdown.Option(key=k, text=v) for k, v in CAMPAIGN_AUTO_JOIN_OPTIONS],
            value="0", width=300,
        )

        def save_sales_info_to_storage():
            """販売情報をベースフォルダに保存"""
            if current_base_folder:
                base_name = Path(current_base_folder).name
                sales_info = {
                    "price_retail": price_field.value or "",
                    "monopoly_hope_flg": "1" if monopoly_switch.value else "0",
                    "drm_hope": drm_dropdown.value or "none",
                    "campaign_kibou_flg": campaign_checkbox.value,
                    "is_coupon_usable": coupon_checkbox.value,
                    "campaign_auto_join_flg_set_days": auto_join_dropdown.value or "0",
                }
                keyword_storage.save_sales_info(base_name, sales_info)
                show_snackbar(f"販売情報を保存しました: {base_name}", ft.colors.GREEN_700)

        def load_sales_info_from_storage():
            """ベースフォルダから販売情報を読み込み"""
            if current_base_folder:
                base_name = Path(current_base_folder).name
                sales_info = keyword_storage.get_sales_info(base_name)
                if sales_info:
                    price_field.value = sales_info.get("price_retail", "")
                    if price_field.value:
                        price_field.border_color = None
                        price_field.error_text = None
                    monopoly_switch.value = sales_info.get("monopoly_hope_flg", "0") == "1"
                    drm_dropdown.value = sales_info.get("drm_hope", "none")
                    campaign_checkbox.value = sales_info.get("campaign_kibou_flg", True)
                    coupon_checkbox.value = sales_info.get("is_coupon_usable", True)
                    auto_join_dropdown.value = sales_info.get("campaign_auto_join_flg_set_days", "0")
                    return True
            return False
        note_field = ft.TextField(label="通信欄", hint_text="FANZA同人への質問や連絡", multiline=True, min_lines=2, max_lines=4, max_length=255, expand=True)

        def load_data_to_ui():
            d = product.data
            title_field.value = d.get("title", "")
            title_ruby_field.value = d.get("title_ruby", "")
            article_type_dropdown.value = d.get("article_type", "cg")
            ai_type_dropdown.value = d.get("ai_generated_type", "1")
            section_dropdown.value = d.get("section", "1")
            keyword_age_dropdown.value = d.get("keyword_age", "156023")
            comment_field.value = d.get("comment", "")
            file_number_field.value = d.get("file_number", "")
            parody_type_val = d.get("parody_type", "")
            parody_type_dropdown.value = parody_type_val
            # パロディ未設定時は赤枠表示
            if parody_type_val:
                parody_type_dropdown.border_color = None
                parody_type_dropdown.error_text = None
            else:
                parody_type_dropdown.border_color = ft.colors.RED
                parody_type_dropdown.error_text = "パロディを選択してください"
            vr_dropdown.value = d.get("vr_compatible", "")
            price_val = d.get("price_retail", "")
            price_field.value = price_val
            # 価格未設定時は赤枠表示
            if price_val:
                price_field.border_color = None
                price_field.error_text = None
            else:
                price_field.border_color = ft.colors.RED
                price_field.error_text = "価格を入力してください"
            monopoly_switch.value = d.get("monopoly_hope_flg", "0") == "1"
            drm_dropdown.value = d.get("drm_hope", "none")
            campaign_checkbox.value = d.get("campaign_kibou_flg", True)
            coupon_checkbox.value = d.get("is_coupon_usable", True)
            auto_join_dropdown.value = d.get("campaign_auto_join_flg_set_days", "0")
            note_field.value = d.get("note", "")

            selected_keywords.clear()
            selected_keywords.extend(d.get("keywords", []))
            update_keyword_chips()

            parody_names = d.get("parody_names", ["", "", "", ""])
            for i, field in enumerate(parody_name_fields):
                field.value = parody_names[i] if i < len(parody_names) else ""

            page.update()

        def save_ui_to_data():
            d = product.data
            d["title"] = title_field.value
            d["title_ruby"] = title_ruby_field.value
            d["article_type"] = article_type_dropdown.value
            d["ai_generated_type"] = ai_type_dropdown.value
            d["section"] = section_dropdown.value
            d["keyword_age"] = keyword_age_dropdown.value
            d["comment"] = comment_field.value
            d["file_number"] = file_number_field.value
            d["parody_type"] = parody_type_dropdown.value
            d["vr_compatible"] = vr_dropdown.value
            d["price_retail"] = price_field.value
            d["monopoly_hope_flg"] = "1" if monopoly_switch.value else "0"
            d["drm_hope"] = drm_dropdown.value
            d["campaign_kibou_flg"] = campaign_checkbox.value
            d["is_coupon_usable"] = coupon_checkbox.value
            d["campaign_auto_join_flg_set_days"] = auto_join_dropdown.value
            d["note"] = note_field.value
            d["keywords"] = selected_keywords.copy()
            d["parody_names"] = [f.value for f in parody_name_fields]

        def save_product(e):
            if not current_base_folder or not current_series_folder or not current_product_folder:
                show_snackbar("商品フォルダを選択してください", ft.colors.ORANGE_700)
                return
            save_ui_to_data()
            product_path = Path(current_base_folder) / current_series_folder / current_product_folder
            info_path = product_path / "info.json"
            product.save_to_file(str(info_path))
            show_snackbar(f"保存しました: {info_path}", ft.colors.GREEN_700)

        def start_auto_input(e):
            if not current_profile_id:
                show_snackbar("プロファイルを選択してください", ft.colors.ORANGE_700)
                return

            # バリデーション
            if not parody_type_dropdown.value:
                show_snackbar("パロディを選択してください", ft.colors.ORANGE_700)
                return
            if not price_field.value:
                show_snackbar("販売価格を入力してください", ft.colors.ORANGE_700)
                return

            save_ui_to_data()

            # 進捗表示用ダイアログ
            progress_text = ft.Text("準備中...")
            progress_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("自動入力実行中"),
                content=ft.Column([
                    ft.ProgressRing(),
                    progress_text,
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, height=100),
            )
            page.overlay.append(progress_dialog)
            progress_dialog.open = True
            page.update()

            def update_progress(message: str):
                progress_text.value = message
                page.update()

            def run_auto_input():
                browser = None
                try:
                    update_progress("ブラウザを起動中...")

                    # AdsPowerブラウザを起動
                    from browser.adspower import AdsPowerBrowser
                    from browser.form_filler import FanzaFormFiller

                    browser = AdsPowerBrowser(ads_api)
                    if not browser.start(current_profile_id):
                        update_progress("ブラウザ起動失敗")
                        time.sleep(2)
                        progress_dialog.open = False
                        page.update()
                        show_snackbar("ブラウザの起動に失敗しました", ft.colors.RED_700)
                        return

                    update_progress("フォームページを開いています...")

                    # フォーム入力
                    filler = FanzaFormFiller(browser.get_driver(), callback=update_progress)
                    filler.navigate_to_form()

                    # データを入力
                    filler.fill_form(product.data)

                    update_progress("完了！ブラウザで確認してください")
                    time.sleep(2)

                    progress_dialog.open = False
                    page.update()
                    show_snackbar("自動入力が完了しました。ブラウザで内容を確認してください。", ft.colors.GREEN_700)

                except Exception as ex:
                    update_progress(f"エラー: {ex}")
                    time.sleep(2)
                    progress_dialog.open = False
                    page.update()
                    show_snackbar(f"エラーが発生しました: {ex}", ft.colors.RED_700)

            # 別スレッドで実行
            thread = threading.Thread(target=run_auto_input, daemon=True)
            thread.start()

        def create_section(title: str, controls: list):
            return ft.Container(
                content=ft.Column([
                    ft.Text(title, size=18, weight=ft.FontWeight.BOLD),
                    ft.Divider(height=1),
                    *controls,
                ]),
                padding=15,
                margin=ft.margin.only(bottom=10),
                border=ft.border.all(1, ft.colors.OUTLINE),
                border_radius=10,
            )

        # 入力タブのレイアウト
        input_content = ft.Column([
            # フォルダ選択
            ft.Container(
                content=ft.Column([
                    ft.Text("作品選択", size=16, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        profile_dropdown,
                        base_folder_dropdown,
                        series_dropdown,
                        product_dropdown,
                        ft.IconButton(
                            ft.icons.REFRESH,
                            tooltip="データ再読み込み",
                            on_click=reload_product_data,
                        ),
                    ], spacing=10, wrap=True),
                    ft.Divider(),
                    ft.Row([
                        ft.ElevatedButton("保存", icon=ft.icons.SAVE, on_click=save_product),
                        ft.ElevatedButton(
                            "自動入力開始",
                            icon=ft.icons.PLAY_ARROW,
                            on_click=start_auto_input,
                            style=ft.ButtonStyle(bgcolor=ft.colors.GREEN_700)
                        ),
                    ], spacing=10),
                ]),
                padding=15,
                bgcolor=ft.colors.SURFACE_VARIANT,
                border_radius=10,
                margin=ft.margin.only(bottom=10),
            ),

            create_section("基本設定", [
                ft.Row([title_field], spacing=10),
                ft.Row([title_ruby_field], spacing=10),
                ft.Row([article_type_dropdown, ai_type_dropdown], spacing=10),
                ft.Row([section_dropdown, keyword_age_dropdown, vr_dropdown], spacing=10),
            ]),

            create_section("作品内容", [
                comment_field,
                ft.Row([file_number_field, ft.Text("※50枚以上のフォルダの画像合計（上限500枚）", size=11, color=ft.colors.GREY)], spacing=10),
            ]),

            create_section("パロディ設定", [
                parody_type_dropdown,
                ft.Row(parody_name_fields, spacing=10, wrap=True),
            ]),

            create_section("キーワード（最大10個）", [
                ft.Row([
                    keyword_count_text,
                    ft.ElevatedButton(
                        "キーワード保存",
                        icon=ft.icons.SAVE,
                        on_click=lambda e: save_keywords_to_storage(),
                        tooltip="ベースフォルダにキーワードを保存",
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                keyword_chips,
                ft.Divider(height=10),
                ft.Text("人気キーワード:", size=12, weight=ft.FontWeight.BOLD),
                keyword_buttons,
                ft.Divider(height=10),
                ft.Text("カテゴリ別キーワード:", size=12, weight=ft.FontWeight.BOLD),
                category_tabs,
            ]),

            create_section("販売情報", [
                ft.Row([
                    ft.Text("※ベースフォルダごとに保存可能", size=11, color=ft.colors.GREY),
                    ft.ElevatedButton(
                        "販売情報保存",
                        icon=ft.icons.SAVE,
                        on_click=lambda e: save_sales_info_to_storage(),
                        tooltip="ベースフォルダに販売情報を保存",
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([price_field, monopoly_switch, drm_dropdown], spacing=20),
                ft.Row([campaign_checkbox, coupon_checkbox], spacing=20),
                auto_join_dropdown,
            ]),

            create_section("通信欄", [note_field]),
        ], scroll=ft.ScrollMode.AUTO)

        return input_content

    # ========== メインレイアウト ==========
    tabs = ft.Tabs(
        selected_index=0,
        tabs=[
            ft.Tab(
                text="作品入力",
                icon=ft.icons.EDIT,
                content=ft.Container(
                    content=create_input_tab(),
                    padding=10,
                ),
            ),
            ft.Tab(
                text="設定",
                icon=ft.icons.SETTINGS,
                content=ft.Container(
                    content=create_settings_tab(),
                    padding=10,
                ),
            ),
        ],
        expand=True,
    )

    page.add(
        ft.Container(
            content=ft.Text("FANZA同人 自動入力ツール", size=24, weight=ft.FontWeight.BOLD),
            padding=10,
        ),
        tabs,
        ft.Container(content=status_text, padding=10, bgcolor=ft.colors.SURFACE_VARIANT),
    )


if __name__ == "__main__":
    ft.app(target=main)
