"""
FANZA同人 自動入力ツール (tkinter版)
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import sys
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
    count_all_images_in_product,
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
            return sorted([f.name for f in path.iterdir() if f.is_dir()])
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
            "price_retail": "800",
            "monopoly_hope_flg": "0",
            "drm_hope": "none",
            "revision_flg": "1",
            "campaign_kibou_flg": True,
            "is_coupon_usable": True,
            "campaign_auto_join_flg_set_days": "0",
            "pre_release_articles_campaign_flg": "1",
            "pre_release_articles_campaign_discount_days": "28",
            "pre_release_articles_campaign_discount_rate": "80",
            "release_date_type": "1",
            "release_date": "",
            "note": "",
        }

    def load_from_file(self, filepath: str):
        with open(filepath, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    def save_to_file(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)


class FanzaAutoInputApp:
    # ダークテーマカラー定義
    COLORS = {
        "bg": "#1a1a2e",           # 背景色（濃い紺）
        "bg_secondary": "#16213e", # セカンダリ背景
        "bg_frame": "#0f3460",     # フレーム背景
        "fg": "#eaeaea",           # テキスト色
        "fg_secondary": "#a0a0a0", # セカンダリテキスト
        "accent": "#e94560",       # アクセント色（赤）
        "accent_hover": "#ff6b6b", # アクセントホバー
        "button_bg": "#0f3460",    # ボタン背景
        "button_fg": "#ffffff",    # ボタンテキスト
        "entry_bg": "#16213e",     # 入力フィールド背景
        "entry_fg": "#ffffff",     # 入力フィールドテキスト
        "select_bg": "#e94560",    # 選択時背景
        "border": "#0f3460",       # ボーダー色
        "success": "#00d9a0",      # 成功色（緑）
        "warning": "#ffc107",      # 警告色（黄）
    }

    def __init__(self, root):
        self.root = root
        self.root.title("FANZA同人 自動入力ツール")
        self.root.geometry("1200x800")

        # ダークテーマを適用
        self.setup_dark_theme()

        # 状態管理
        self.product = ProductData()
        self.profile_manager = ProfileManager()
        self.variable_manager = VariableManager()
        self.keyword_storage = KeywordStorage()
        self.ads_api = AdsPowerAPI(self.profile_manager.api_url)
        self.furigana_converter = FuriganaConverter(self.profile_manager.openai_api_key)

        # 現在選択中の状態
        self.current_profile_id = None
        self.current_base_folder = None
        self.current_series_folder = None
        self.current_product_folder = None
        self.selected_keywords = []
        self.keyword_buttons = {}  # キーワードID -> ボタンのリスト（複数タブ対応）

        # UI変数
        self.title_var = tk.StringVar()
        self.title_ruby_var = tk.StringVar()
        self.price_var = tk.StringVar(value="800")
        self.monopoly_var = tk.BooleanVar(value=False)
        self.campaign_var = tk.BooleanVar(value=True)
        self.coupon_var = tk.BooleanVar(value=True)
        self.file_number_var = tk.StringVar()
        self.discount_enabled_var = tk.BooleanVar(value=True)
        self.release_date_type_var = tk.StringVar(value="1")

        # 予約作品用UI変数
        self.trailer_title_var = tk.StringVar()
        self.trailer_title_ruby_var = tk.StringVar()
        self.trailer_price_undecided_var = tk.BooleanVar(value=True)  # 販売価格未定（デフォルトON）
        self.trailer_price_var = tk.StringVar(value="800")
        self.trailer_monopoly_var = tk.BooleanVar(value=False)
        self.trailer_file_number_var = tk.StringVar()
        self.trailer_release_date_type_var = tk.StringVar(value="1")  # 予告開始日指定（デフォルト: 最短で公開）
        self.trailer_release_undecided_var = tk.BooleanVar(value=True)  # 配信予定未定（デフォルトON）
        self.trailer_selected_keywords = []
        self.trailer_keyword_buttons = {}

        self.create_ui()
        self.load_initial_data()

    def setup_dark_theme(self):
        """ダークテーマのスタイルを設定"""
        c = self.COLORS

        # ルートウィンドウの背景色
        self.root.configure(bg=c["bg"])

        # ttkスタイルを設定
        style = ttk.Style()
        style.theme_use("clam")

        # 共通スタイル
        style.configure(".",
            background=c["bg"],
            foreground=c["fg"],
            fieldbackground=c["entry_bg"],
            font=("Meiryo UI", 10)
        )

        # フレーム
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabelframe", background=c["bg"], foreground=c["fg"])
        style.configure("TLabelframe.Label",
            background=c["bg"],
            foreground=c["accent"],
            font=("Meiryo UI", 11, "bold")
        )

        # ラベル
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("Header.TLabel",
            background=c["bg"],
            foreground=c["accent"],
            font=("Meiryo UI", 12, "bold")
        )
        style.configure("Status.TLabel",
            background=c["bg_secondary"],
            foreground=c["fg"],
            padding=5
        )

        # ボタン
        style.configure("TButton",
            background=c["button_bg"],
            foreground=c["button_fg"],
            padding=(10, 5),
            font=("Meiryo UI", 10)
        )
        style.map("TButton",
            background=[("active", c["accent"]), ("pressed", c["accent_hover"])],
            foreground=[("active", "#ffffff")]
        )

        # アクセントボタン（重要なアクション用）
        style.configure("Accent.TButton",
            background=c["accent"],
            foreground="#ffffff",
            padding=(15, 8),
            font=("Meiryo UI", 11, "bold")
        )
        style.map("Accent.TButton",
            background=[("active", c["accent_hover"]), ("pressed", "#ff8888")]
        )

        # 成功ボタン
        style.configure("Success.TButton",
            background=c["success"],
            foreground="#ffffff",
            padding=(15, 8),
            font=("Meiryo UI", 11, "bold")
        )
        style.map("Success.TButton",
            background=[("active", "#00ffb3")]
        )

        # 選択済みキーワードボタン
        style.configure("Selected.TButton",
            background=c["accent"],
            foreground="#ffffff",
            padding=(6, 4),
            font=("Meiryo UI", 9)
        )
        style.map("Selected.TButton",
            background=[("active", c["accent_hover"])]
        )

        # キーワードボタン（通常）
        style.configure("Keyword.TButton",
            background=c["button_bg"],
            foreground=c["button_fg"],
            padding=(6, 4),
            font=("Meiryo UI", 9)
        )
        style.map("Keyword.TButton",
            background=[("active", c["bg_frame"])]
        )

        # 入力フィールド
        style.configure("TEntry",
            fieldbackground=c["entry_bg"],
            foreground=c["entry_fg"],
            insertcolor=c["fg"],
            padding=5
        )

        # コンボボックス
        style.configure("TCombobox",
            fieldbackground=c["entry_bg"],
            background=c["button_bg"],
            foreground=c["entry_fg"],
            arrowcolor=c["fg"],
            padding=5
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", c["entry_bg"])],
            selectbackground=[("readonly", c["select_bg"])],
            selectforeground=[("readonly", "#ffffff")]
        )

        # ノートブック（タブ）
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab",
            background=c["bg_secondary"],
            foreground=c["fg"],
            padding=(15, 8),
            font=("Meiryo UI", 10)
        )
        style.map("TNotebook.Tab",
            background=[("selected", c["accent"]), ("active", c["bg_frame"])],
            foreground=[("selected", "#ffffff")],
            expand=[("selected", [1, 1, 1, 0])]
        )

        # チェックボタン
        style.configure("TCheckbutton",
            background=c["bg"],
            foreground=c["fg"],
            font=("Meiryo UI", 10)
        )
        style.map("TCheckbutton",
            background=[("active", c["bg"])],
            foreground=[("active", c["accent"])]
        )

        # ラジオボタン
        style.configure("TRadiobutton",
            background=c["bg"],
            foreground=c["fg"],
            font=("Meiryo UI", 10)
        )
        style.map("TRadiobutton",
            background=[("active", c["bg"])],
            foreground=[("active", c["accent"])]
        )

        # スクロールバー
        style.configure("TScrollbar",
            background=c["bg_secondary"],
            troughcolor=c["bg"],
            arrowcolor=c["fg"]
        )
        style.map("TScrollbar",
            background=[("active", c["accent"])]
        )

        # プログレスバー
        style.configure("TProgressbar",
            background=c["accent"],
            troughcolor=c["bg_secondary"]
        )

    def create_ui(self):
        # メインノートブック（タブ）
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 作品入力タブ
        self.input_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.input_frame, text="作品入力")
        self.create_input_tab()

        # 予約作品入力タブ
        self.trailer_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.trailer_frame, text="予約作品入力")
        self.create_trailer_input_tab()

        # 設定タブ
        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="設定")
        self.create_settings_tab()

        # ステータスバー
        self.status_var = tk.StringVar(value="準備完了")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel")
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, ipady=5)

    def create_input_tab(self):
        c = self.COLORS
        # スクロール可能なキャンバス
        canvas = tk.Canvas(self.input_frame, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.input_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # マウスホイールスクロール（キャンバス上のみ）
        self.input_canvas = canvas
        def on_mousewheel(event):
            # Textウィジェット上ではスクロールしない
            widget = event.widget
            if isinstance(widget, tk.Text):
                return
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)

        # 子ウィジェットにもバインド
        def bind_mousewheel(widget):
            if not isinstance(widget, tk.Text):
                widget.bind("<MouseWheel>", on_mousewheel)
            for child in widget.winfo_children():
                bind_mousewheel(child)

        scrollable_frame.bind("<Map>", lambda e: bind_mousewheel(scrollable_frame))

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === 作品選択セクション ===
        select_frame = ttk.LabelFrame(scrollable_frame, text="作品選択", padding=10)
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        # プロファイル選択
        ttk.Label(select_frame, text="プロファイル:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.profile_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.profile_combo.grid(row=0, column=1, padx=5, pady=2)
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_change)

        # ベースフォルダ選択
        ttk.Label(select_frame, text="ベースフォルダ:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.base_folder_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.base_folder_combo.grid(row=0, column=3, padx=5, pady=2)
        self.base_folder_combo.bind("<<ComboboxSelected>>", self.on_base_folder_change)

        # シリーズ選択
        ttk.Label(select_frame, text="シリーズ:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.series_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.series_combo.grid(row=1, column=1, padx=5, pady=2)
        self.series_combo.bind("<<ComboboxSelected>>", self.on_series_change)

        # 商品フォルダ選択
        ttk.Label(select_frame, text="商品フォルダ:").grid(row=1, column=2, sticky=tk.W, padx=5)
        self.product_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.product_combo.grid(row=1, column=3, padx=5, pady=2)
        self.product_combo.bind("<<ComboboxSelected>>", self.on_product_change)

        # ボタン
        btn_frame = ttk.Frame(select_frame)
        btn_frame.grid(row=2, column=0, columnspan=4, pady=10)
        ttk.Button(btn_frame, text="保存", command=self.save_product, style="Success.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="自動入力開始", command=self.start_auto_input, style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="再読み込み", command=self.reload_product).pack(side=tk.LEFT, padx=5)

        # === 基本設定セクション ===
        basic_frame = ttk.LabelFrame(scrollable_frame, text="基本設定", padding=10)
        basic_frame.pack(fill=tk.X, padx=5, pady=5)

        # タイトル
        ttk.Label(basic_frame, text="タイトル:").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Entry(basic_frame, textvariable=self.title_var, width=60).grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # ふりがな
        ttk.Label(basic_frame, text="ふりがな:").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Entry(basic_frame, textvariable=self.title_ruby_var, width=60).grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # 作品形式
        ttk.Label(basic_frame, text="作品形式:").grid(row=2, column=0, sticky=tk.W, padx=5)
        self.article_combo = ttk.Combobox(basic_frame, width=20, state="readonly")
        self.article_combo['values'] = [v for k, v in ARTICLE_TYPES]
        self.article_combo.current(1)  # デフォルト: CG
        self.article_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        # AI利用
        ttk.Label(basic_frame, text="AI利用:").grid(row=2, column=2, sticky=tk.W, padx=5)
        self.ai_combo = ttk.Combobox(basic_frame, width=35, state="readonly")
        self.ai_combo['values'] = [v for k, v in AI_GENERATED_TYPES]
        self.ai_combo.current(1)  # デフォルト: AIで作品を生成している
        self.ai_combo.grid(row=2, column=3, sticky=tk.W, padx=5, pady=2)

        # 作品区分
        ttk.Label(basic_frame, text="作品区分:").grid(row=3, column=0, sticky=tk.W, padx=5)
        self.section_combo = ttk.Combobox(basic_frame, width=15, state="readonly")
        self.section_combo['values'] = [v for k, v in SECTIONS]
        self.section_combo.current(0)  # デフォルト: 男性向け
        self.section_combo.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

        # 年齢指定
        ttk.Label(basic_frame, text="年齢指定:").grid(row=3, column=2, sticky=tk.W, padx=5)
        self.age_combo = ttk.Combobox(basic_frame, width=15, state="readonly")
        self.age_combo['values'] = [v for k, v in KEYWORD_AGES]
        self.age_combo.current(0)  # デフォルト: 成人向け
        self.age_combo.grid(row=3, column=3, sticky=tk.W, padx=5, pady=2)

        # === 作品内容セクション ===
        content_frame = ttk.LabelFrame(scrollable_frame, text="作品内容", padding=10)
        content_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(content_frame, text="説明文:").grid(row=0, column=0, sticky=tk.NW, padx=5)

        # 説明文フレーム（スクロールバー付き）
        comment_frame = ttk.Frame(content_frame)
        comment_frame.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)

        comment_scrollbar = ttk.Scrollbar(comment_frame)
        comment_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.comment_text = tk.Text(
            comment_frame, width=80, height=12,
            yscrollcommand=comment_scrollbar.set,
            bg=c["entry_bg"], fg=c["entry_fg"],
            insertbackground=c["fg"],
            selectbackground=c["accent"],
            selectforeground="#ffffff",
            font=("Meiryo UI", 10),
            relief=tk.FLAT,
            padx=8, pady=8
        )
        self.comment_text.pack(side=tk.LEFT, fill=tk.BOTH)
        comment_scrollbar.config(command=self.comment_text.yview)

        # 説明文内でのマウスホイールを独立させる
        def on_comment_mousewheel(event):
            self.comment_text.yview_scroll(int(-1*(event.delta/120)), "units")
            return "break"  # 親へのイベント伝播を止める
        self.comment_text.bind("<MouseWheel>", on_comment_mousewheel)

        # サイズ調整ボタン
        size_frame = ttk.Frame(content_frame)
        size_frame.grid(row=0, column=2, sticky=tk.N, padx=5)
        ttk.Button(size_frame, text="大", width=3, command=lambda: self.resize_comment(16)).pack(pady=2)
        ttk.Button(size_frame, text="中", width=3, command=lambda: self.resize_comment(12)).pack(pady=2)
        ttk.Button(size_frame, text="小", width=3, command=lambda: self.resize_comment(8)).pack(pady=2)

        ttk.Label(content_frame, text="枚数:").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Entry(content_frame, textvariable=self.file_number_var, width=15, state="readonly").grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        # === パロディ設定セクション ===
        parody_frame = ttk.LabelFrame(scrollable_frame, text="パロディ設定", padding=10)
        parody_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(parody_frame, text="パロディ:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.parody_combo = ttk.Combobox(parody_frame, width=20, state="readonly")
        self.parody_combo['values'] = ["(選択してください)"] + [v for k, v in PARODY_TYPES]
        self.parody_combo.current(0)
        self.parody_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        self.parody_entries = []
        for i in range(4):
            ttk.Label(parody_frame, text=f"詳細{i+1}:").grid(row=1, column=i*2, sticky=tk.W, padx=5)
            entry = ttk.Entry(parody_frame, width=20)
            entry.grid(row=1, column=i*2+1, sticky=tk.W, padx=2, pady=2)
            self.parody_entries.append(entry)

        # === キーワードセクション ===
        keyword_frame = ttk.LabelFrame(scrollable_frame, text="キーワード（最大10個）", padding=10)
        keyword_frame.pack(fill=tk.X, padx=5, pady=5)

        # 選択状態表示
        keyword_status_frame = ttk.Frame(keyword_frame)
        keyword_status_frame.pack(fill=tk.X, pady=5)

        self.keyword_label = ttk.Label(keyword_status_frame, text="選択中: 0/10")
        self.keyword_label.pack(side=tk.LEFT)

        ttk.Button(keyword_status_frame, text="キーワード保存", command=self.save_keywords_to_storage, style="Success.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Button(keyword_status_frame, text="クリア", command=self.clear_keywords).pack(side=tk.RIGHT, padx=5)

        self.keyword_display = ttk.Label(keyword_frame, text="", wraplength=900, foreground=c["success"])
        self.keyword_display.pack(anchor=tk.W, pady=5)

        # カテゴリタブ
        self.keyword_notebook = ttk.Notebook(keyword_frame)
        self.keyword_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # 人気キーワードタブ
        popular_tab = ttk.Frame(self.keyword_notebook)
        self.keyword_notebook.add(popular_tab, text="人気")
        self.create_keyword_buttons(popular_tab, [(kid, name, True) for kid, name in POPULAR_KEYWORDS])

        # 各カテゴリタブ
        for cat_id, cat_name in KEYWORD_CATEGORIES:
            tab = ttk.Frame(self.keyword_notebook)
            self.keyword_notebook.add(tab, text=cat_name[:6])  # タブ名を短縮
            keywords = KEYWORDS_BY_CATEGORY.get(cat_id, [])
            self.create_keyword_buttons(tab, keywords)

        # === 販売情報セクション ===
        sales_frame = ttk.LabelFrame(scrollable_frame, text="販売情報", padding=10)
        sales_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(sales_frame, text="販売価格:").grid(row=0, column=0, sticky=tk.W, padx=5)
        price_entry = ttk.Entry(sales_frame, textvariable=self.price_var, width=15)
        price_entry.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        ttk.Label(sales_frame, text="円（税抜）").grid(row=0, column=2, sticky=tk.W)

        ttk.Checkbutton(sales_frame, text="専売希望", variable=self.monopoly_var).grid(row=0, column=3, padx=20)

        ttk.Label(sales_frame, text="作品保護:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.drm_combo = ttk.Combobox(sales_frame, width=20, state="readonly")
        self.drm_combo['values'] = [v for k, v in DRM_OPTIONS]
        self.drm_combo.current(0)  # デフォルト: なし
        self.drm_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Checkbutton(sales_frame, text="キャンペーン参加", variable=self.campaign_var).grid(row=1, column=3, padx=20)
        ttk.Checkbutton(sales_frame, text="クーポン参加", variable=self.coupon_var).grid(row=1, column=4, padx=20)

        ttk.Label(sales_frame, text="自動参加:").grid(row=2, column=0, sticky=tk.W, padx=5)
        self.auto_join_combo = ttk.Combobox(sales_frame, width=30, state="readonly")
        self.auto_join_combo['values'] = [v for k, v in CAMPAIGN_AUTO_JOIN_OPTIONS]
        self.auto_join_combo.current(0)  # デフォルト: 発売からすぐに自動参加する
        self.auto_join_combo.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=5, pady=2)

        # 割引設定
        ttk.Label(sales_frame, text="割引設定:").grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Checkbutton(sales_frame, text="設定する", variable=self.discount_enabled_var).grid(row=3, column=1, sticky=tk.W, padx=5)

        discount_detail_frame = ttk.Frame(sales_frame)
        discount_detail_frame.grid(row=3, column=2, columnspan=3, sticky=tk.W, padx=5)

        ttk.Label(discount_detail_frame, text="実施期間:").pack(side=tk.LEFT)
        self.discount_days_combo = ttk.Combobox(discount_detail_frame, width=5, state="readonly")
        self.discount_days_combo['values'] = [str(i) for i in range(1, 29)]
        self.discount_days_combo.current(27)  # デフォルト: 28日（インデックス27）
        self.discount_days_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(discount_detail_frame, text="日").pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(discount_detail_frame, text="割引率:").pack(side=tk.LEFT)
        self.discount_rate_combo = ttk.Combobox(discount_detail_frame, width=5, state="readonly")
        self.discount_rate_combo['values'] = ["10", "15", "20", "25", "30", "35", "40", "45", "50", "55", "60", "65", "70", "75", "80", "85", "90", "95"]
        self.discount_rate_combo.current(14)  # デフォルト: 80%（インデックス14）
        self.discount_rate_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(discount_detail_frame, text="%").pack(side=tk.LEFT)

        # 配信開始日指定
        ttk.Label(sales_frame, text="配信開始:").grid(row=4, column=0, sticky=tk.W, padx=5)
        release_frame = ttk.Frame(sales_frame)
        release_frame.grid(row=4, column=1, columnspan=4, sticky=tk.W, padx=5, pady=2)

        ttk.Radiobutton(release_frame, text="最短で公開", variable=self.release_date_type_var, value="1").pack(side=tk.LEFT)
        ttk.Radiobutton(release_frame, text="日付を指定して公開", variable=self.release_date_type_var, value="2").pack(side=tk.LEFT, padx=10)

        # === 通信欄セクション ===
        note_frame = ttk.LabelFrame(scrollable_frame, text="通信欄", padding=10)
        note_frame.pack(fill=tk.X, padx=5, pady=5)

        self.note_text = tk.Text(
            note_frame, width=80, height=3,
            bg=c["entry_bg"], fg=c["entry_fg"],
            insertbackground=c["fg"],
            selectbackground=c["accent"],
            selectforeground="#ffffff",
            font=("Meiryo UI", 10),
            relief=tk.FLAT,
            padx=8, pady=8
        )
        self.note_text.pack(padx=5, pady=2)

    def create_trailer_input_tab(self):
        """予約作品入力タブを作成"""
        c = self.COLORS
        # スクロール可能なキャンバス
        canvas = tk.Canvas(self.trailer_frame, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.trailer_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # マウスホイールスクロール
        self.trailer_canvas = canvas
        def on_mousewheel(event):
            widget = event.widget
            if isinstance(widget, tk.Text):
                return
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)

        def bind_mousewheel(widget):
            if not isinstance(widget, tk.Text):
                widget.bind("<MouseWheel>", on_mousewheel)
            for child in widget.winfo_children():
                bind_mousewheel(child)

        scrollable_frame.bind("<Map>", lambda e: bind_mousewheel(scrollable_frame))

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === 作品選択セクション ===
        select_frame = ttk.LabelFrame(scrollable_frame, text="予約作品選択", padding=10)
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        # プロファイル選択
        ttk.Label(select_frame, text="プロファイル:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.trailer_profile_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.trailer_profile_combo.grid(row=0, column=1, padx=5, pady=2)
        self.trailer_profile_combo.bind("<<ComboboxSelected>>", self.on_trailer_profile_change)

        # ベースフォルダ選択
        ttk.Label(select_frame, text="ベースフォルダ:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.trailer_base_folder_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.trailer_base_folder_combo.grid(row=0, column=3, padx=5, pady=2)
        self.trailer_base_folder_combo.bind("<<ComboboxSelected>>", self.on_trailer_base_folder_change)

        # シリーズ選択
        ttk.Label(select_frame, text="シリーズ:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.trailer_series_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.trailer_series_combo.grid(row=1, column=1, padx=5, pady=2)
        self.trailer_series_combo.bind("<<ComboboxSelected>>", self.on_trailer_series_change)

        # 商品フォルダ選択
        ttk.Label(select_frame, text="商品フォルダ:").grid(row=1, column=2, sticky=tk.W, padx=5)
        self.trailer_product_combo = ttk.Combobox(select_frame, width=25, state="readonly")
        self.trailer_product_combo.grid(row=1, column=3, padx=5, pady=2)
        self.trailer_product_combo.bind("<<ComboboxSelected>>", self.on_trailer_product_change)

        # ボタン
        btn_frame = ttk.Frame(select_frame)
        btn_frame.grid(row=2, column=0, columnspan=4, pady=10)
        ttk.Button(btn_frame, text="保存", command=self.save_trailer_product, style="Success.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="予約作品入力開始", command=self.start_trailer_auto_input, style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="再読み込み", command=self.reload_trailer_product).pack(side=tk.LEFT, padx=5)

        # === 基本設定セクション ===
        basic_frame = ttk.LabelFrame(scrollable_frame, text="基本設定", padding=10)
        basic_frame.pack(fill=tk.X, padx=5, pady=5)

        # タイトル
        ttk.Label(basic_frame, text="タイトル:").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Entry(basic_frame, textvariable=self.trailer_title_var, width=60).grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # ふりがな
        ttk.Label(basic_frame, text="ふりがな:").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Entry(basic_frame, textvariable=self.trailer_title_ruby_var, width=60).grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # 作品形式
        ttk.Label(basic_frame, text="作品形式:").grid(row=2, column=0, sticky=tk.W, padx=5)
        self.trailer_article_combo = ttk.Combobox(basic_frame, width=20, state="readonly")
        self.trailer_article_combo['values'] = [v for k, v in ARTICLE_TYPES]
        self.trailer_article_combo.current(1)  # デフォルト: CG
        self.trailer_article_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        # AI利用
        ttk.Label(basic_frame, text="AI利用:").grid(row=2, column=2, sticky=tk.W, padx=5)
        self.trailer_ai_combo = ttk.Combobox(basic_frame, width=35, state="readonly")
        self.trailer_ai_combo['values'] = [v for k, v in AI_GENERATED_TYPES]
        self.trailer_ai_combo.current(1)  # デフォルト: AIで作品を生成している
        self.trailer_ai_combo.grid(row=2, column=3, sticky=tk.W, padx=5, pady=2)

        # 作品区分
        ttk.Label(basic_frame, text="作品区分:").grid(row=3, column=0, sticky=tk.W, padx=5)
        self.trailer_section_combo = ttk.Combobox(basic_frame, width=15, state="readonly")
        self.trailer_section_combo['values'] = [v for k, v in SECTIONS]
        self.trailer_section_combo.current(0)  # デフォルト: 男性向け
        self.trailer_section_combo.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

        # 年齢指定
        ttk.Label(basic_frame, text="年齢指定:").grid(row=3, column=2, sticky=tk.W, padx=5)
        self.trailer_age_combo = ttk.Combobox(basic_frame, width=15, state="readonly")
        self.trailer_age_combo['values'] = [v for k, v in KEYWORD_AGES]
        self.trailer_age_combo.current(0)  # デフォルト: 成人向け
        self.trailer_age_combo.grid(row=3, column=3, sticky=tk.W, padx=5, pady=2)

        # === 作品内容セクション ===
        content_frame = ttk.LabelFrame(scrollable_frame, text="作品内容", padding=10)
        content_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(content_frame, text="説明文:").grid(row=0, column=0, sticky=tk.NW, padx=5)

        # 説明文フレーム
        comment_frame = ttk.Frame(content_frame)
        comment_frame.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)

        comment_scrollbar = ttk.Scrollbar(comment_frame)
        comment_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.trailer_comment_text = tk.Text(
            comment_frame, width=80, height=12,
            yscrollcommand=comment_scrollbar.set,
            bg=c["entry_bg"], fg=c["entry_fg"],
            insertbackground=c["fg"],
            selectbackground=c["accent"],
            selectforeground="#ffffff",
            font=("Meiryo UI", 10),
            relief=tk.FLAT,
            padx=8, pady=8
        )
        self.trailer_comment_text.pack(side=tk.LEFT, fill=tk.BOTH)
        comment_scrollbar.config(command=self.trailer_comment_text.yview)

        def on_comment_mousewheel(event):
            self.trailer_comment_text.yview_scroll(int(-1*(event.delta/120)), "units")
            return "break"
        self.trailer_comment_text.bind("<MouseWheel>", on_comment_mousewheel)

        # サイズ調整ボタン
        size_frame = ttk.Frame(content_frame)
        size_frame.grid(row=0, column=2, sticky=tk.N, padx=5)
        ttk.Button(size_frame, text="大", width=3, command=lambda: self.trailer_comment_text.config(height=16)).pack(pady=2)
        ttk.Button(size_frame, text="中", width=3, command=lambda: self.trailer_comment_text.config(height=12)).pack(pady=2)
        ttk.Button(size_frame, text="小", width=3, command=lambda: self.trailer_comment_text.config(height=8)).pack(pady=2)

        ttk.Label(content_frame, text="枚数:").grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Entry(content_frame, textvariable=self.trailer_file_number_var, width=15, state="readonly").grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        # === パロディ設定セクション ===
        parody_frame = ttk.LabelFrame(scrollable_frame, text="パロディ設定", padding=10)
        parody_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(parody_frame, text="パロディ:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.trailer_parody_combo = ttk.Combobox(parody_frame, width=20, state="readonly")
        self.trailer_parody_combo['values'] = ["(選択してください)"] + [v for k, v in PARODY_TYPES]
        self.trailer_parody_combo.current(0)
        self.trailer_parody_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        self.trailer_parody_entries = []
        for i in range(4):
            ttk.Label(parody_frame, text=f"詳細{i+1}:").grid(row=1, column=i*2, sticky=tk.W, padx=5)
            entry = ttk.Entry(parody_frame, width=20)
            entry.grid(row=1, column=i*2+1, sticky=tk.W, padx=2, pady=2)
            self.trailer_parody_entries.append(entry)

        # === キーワードセクション ===
        keyword_frame = ttk.LabelFrame(scrollable_frame, text="キーワード（最大10個）", padding=10)
        keyword_frame.pack(fill=tk.X, padx=5, pady=5)

        # 選択状態表示
        keyword_status_frame = ttk.Frame(keyword_frame)
        keyword_status_frame.pack(fill=tk.X, pady=5)

        self.trailer_keyword_label = ttk.Label(keyword_status_frame, text="選択中: 0/10")
        self.trailer_keyword_label.pack(side=tk.LEFT)

        ttk.Button(keyword_status_frame, text="キーワード保存", command=self.save_trailer_keywords_to_storage, style="Success.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Button(keyword_status_frame, text="クリア", command=self.clear_trailer_keywords).pack(side=tk.RIGHT, padx=5)

        self.trailer_keyword_display = ttk.Label(keyword_frame, text="", wraplength=900, foreground=c["success"])
        self.trailer_keyword_display.pack(anchor=tk.W, pady=5)

        # カテゴリタブ
        self.trailer_keyword_notebook = ttk.Notebook(keyword_frame)
        self.trailer_keyword_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # 人気キーワードタブ
        popular_tab = ttk.Frame(self.trailer_keyword_notebook)
        self.trailer_keyword_notebook.add(popular_tab, text="人気")
        self.create_trailer_keyword_buttons(popular_tab, [(kid, name, True) for kid, name in POPULAR_KEYWORDS])

        # 各カテゴリタブ
        for cat_id, cat_name in KEYWORD_CATEGORIES:
            tab = ttk.Frame(self.trailer_keyword_notebook)
            self.trailer_keyword_notebook.add(tab, text=cat_name[:6])
            keywords = KEYWORDS_BY_CATEGORY.get(cat_id, [])
            self.create_trailer_keyword_buttons(tab, keywords)

        # === 販売情報セクション ===
        sales_frame = ttk.LabelFrame(scrollable_frame, text="販売情報", padding=10)
        sales_frame.pack(fill=tk.X, padx=5, pady=5)

        # 販売価格（未定チェックボックス付き）
        ttk.Label(sales_frame, text="販売価格:").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Checkbutton(sales_frame, text="未定", variable=self.trailer_price_undecided_var,
                       command=self.toggle_trailer_price).grid(row=0, column=1, sticky=tk.W, padx=5)
        self.trailer_price_entry = ttk.Entry(sales_frame, textvariable=self.trailer_price_var, width=15, state="disabled")
        self.trailer_price_entry.grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(sales_frame, text="円（税抜）").grid(row=0, column=3, sticky=tk.W)

        ttk.Checkbutton(sales_frame, text="専売希望", variable=self.trailer_monopoly_var).grid(row=0, column=4, padx=20)

        ttk.Label(sales_frame, text="作品保護:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.trailer_drm_combo = ttk.Combobox(sales_frame, width=20, state="readonly")
        self.trailer_drm_combo['values'] = [v for k, v in DRM_OPTIONS]
        self.trailer_drm_combo.current(0)  # デフォルト: なし
        self.trailer_drm_combo.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=5, pady=2)

        # 予告開始日指定
        ttk.Label(sales_frame, text="予告開始日:").grid(row=2, column=0, sticky=tk.W, padx=5)
        trailer_release_frame = ttk.Frame(sales_frame)
        trailer_release_frame.grid(row=2, column=1, columnspan=4, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(trailer_release_frame, text="最短で公開", variable=self.trailer_release_date_type_var, value="1").pack(side=tk.LEFT)
        ttk.Radiobutton(trailer_release_frame, text="日付を指定して公開", variable=self.trailer_release_date_type_var, value="2").pack(side=tk.LEFT, padx=10)

        # 配信予定
        ttk.Label(sales_frame, text="配信予定:").grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Checkbutton(sales_frame, text="未定", variable=self.trailer_release_undecided_var).grid(row=3, column=1, sticky=tk.W, padx=5)

        # === 通信欄セクション ===
        note_frame = ttk.LabelFrame(scrollable_frame, text="通信欄", padding=10)
        note_frame.pack(fill=tk.X, padx=5, pady=5)

        self.trailer_note_text = tk.Text(
            note_frame, width=80, height=3,
            bg=c["entry_bg"], fg=c["entry_fg"],
            insertbackground=c["fg"],
            selectbackground=c["accent"],
            selectforeground="#ffffff",
            font=("Meiryo UI", 10),
            relief=tk.FLAT,
            padx=8, pady=8
        )
        self.trailer_note_text.pack(padx=5, pady=2)

    def create_trailer_keyword_buttons(self, parent, keywords):
        """予約作品用キーワードボタンを作成"""
        c = self.COLORS
        canvas = tk.Canvas(parent, height=180, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        btn_frame = ttk.Frame(canvas)

        btn_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=btn_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            return "break"

        canvas.bind("<MouseWheel>", on_mousewheel)
        btn_frame.bind("<MouseWheel>", on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ボタンを配置（1行に7個、幅を小さく）
        cols = 7
        for i, item in enumerate(keywords):
            if len(item) == 2:
                kid, name = item
            else:
                kid, name, _ = item

            # 長い名前は短縮（8文字まで）
            display_name = name[:8] + ".." if len(name) > 8 else name

            btn = ttk.Button(
                btn_frame,
                text=display_name,
                width=10,
                style="Keyword.TButton",
                command=lambda k=kid: self.toggle_trailer_keyword(k)
            )
            btn.grid(row=i//cols, column=i%cols, padx=1, pady=1, sticky="w")

            if kid not in self.trailer_keyword_buttons:
                self.trailer_keyword_buttons[kid] = []
            self.trailer_keyword_buttons[kid].append(btn)

            btn.bind("<MouseWheel>", on_mousewheel)

    def toggle_trailer_keyword(self, keyword_id):
        """予約作品用キーワードをトグル"""
        if keyword_id in self.trailer_selected_keywords:
            self.trailer_selected_keywords.remove(keyword_id)
        elif len(self.trailer_selected_keywords) < 10:
            self.trailer_selected_keywords.append(keyword_id)
        self.update_trailer_keyword_display()
        self.update_trailer_keyword_button_styles()

    def clear_trailer_keywords(self):
        """予約作品用キーワードをクリア"""
        self.trailer_selected_keywords.clear()
        self.update_trailer_keyword_display()
        self.update_trailer_keyword_button_styles()

    def update_trailer_keyword_display(self):
        """予約作品用キーワード表示を更新"""
        names = [get_keyword_name(k) for k in self.trailer_selected_keywords]
        self.trailer_keyword_display.config(text=", ".join(names) if names else "(なし)")
        self.trailer_keyword_label.config(text=f"選択中: {len(self.trailer_selected_keywords)}/10")

    def update_trailer_keyword_button_styles(self):
        """予約作品用キーワードボタンのスタイルを更新"""
        for kid, btn_list in self.trailer_keyword_buttons.items():
            style = "Selected.TButton" if kid in self.trailer_selected_keywords else "Keyword.TButton"
            for btn in btn_list:
                btn.configure(style=style)

    def toggle_trailer_price(self):
        """予約作品の価格入力の有効/無効を切り替え"""
        if self.trailer_price_undecided_var.get():
            self.trailer_price_entry.config(state="disabled")
        else:
            self.trailer_price_entry.config(state="normal")

    def on_trailer_profile_change(self, event):
        """予約作品タブでプロファイル選択時"""
        idx = self.trailer_profile_combo.current()
        if idx >= 0:
            profile = self.profile_manager.profiles[idx]
            self.trailer_current_profile_id = profile.profile_id
            self.trailer_base_folder_combo['values'] = [Path(f).name for f in profile.folders]
            self.trailer_base_folder_combo.set('')
            self.trailer_series_combo['values'] = []
            self.trailer_series_combo.set('')
            self.trailer_product_combo['values'] = []
            self.trailer_product_combo.set('')

    def on_trailer_base_folder_change(self, event):
        """予約作品タブでベースフォルダ選択時"""
        idx = self.trailer_profile_combo.current()
        base_idx = self.trailer_base_folder_combo.current()
        if idx >= 0 and base_idx >= 0:
            profile = self.profile_manager.profiles[idx]
            self.trailer_current_base_folder = profile.folders[base_idx]
            subfolders = get_subfolders(self.trailer_current_base_folder)
            self.trailer_series_combo['values'] = subfolders
            self.trailer_series_combo.set('')
            self.trailer_product_combo['values'] = []
            self.trailer_product_combo.set('')

            self.trailer_selected_keywords = []
            self.update_trailer_keyword_display()
            self.update_trailer_keyword_button_styles()

    def on_trailer_series_change(self, event):
        """予約作品タブでシリーズ選択時"""
        self.trailer_current_series_folder = self.trailer_series_combo.get()
        if hasattr(self, 'trailer_current_base_folder') and self.trailer_current_series_folder:
            series_path = Path(self.trailer_current_base_folder) / self.trailer_current_series_folder
            subfolders = get_subfolders(str(series_path))
            self.trailer_product_combo['values'] = subfolders
            self.trailer_product_combo.set('')

            # キーワード読み込み
            if hasattr(self, 'trailer_current_profile_id'):
                saved_keywords = self.keyword_storage.get_keywords_by_profile_series(
                    self.trailer_current_profile_id, self.trailer_current_series_folder
                )
                self.trailer_selected_keywords = saved_keywords.copy() if saved_keywords else []
                self.update_trailer_keyword_display()
                self.update_trailer_keyword_button_styles()

    def on_trailer_product_change(self, event):
        """予約作品タブで商品フォルダ選択時"""
        self.trailer_current_product_folder = self.trailer_product_combo.get()
        self.load_trailer_product_data()

    def load_trailer_product_data(self):
        """予約作品のデータを読み込み"""
        if not (hasattr(self, 'trailer_current_base_folder') and
                hasattr(self, 'trailer_current_series_folder') and
                hasattr(self, 'trailer_current_product_folder')):
            return

        if not (self.trailer_current_base_folder and self.trailer_current_series_folder and self.trailer_current_product_folder):
            return

        # タイトル自動生成
        masked_name = mask_second_char(self.trailer_current_product_folder)
        title = f"{self.trailer_current_series_folder} {masked_name}"
        self.trailer_title_var.set(title)

        # 商品パス
        product_path = Path(self.trailer_current_base_folder) / self.trailer_current_series_folder / self.trailer_current_product_folder

        # 説明文読み込み
        description = read_description_file(product_path)
        if description:
            processed = process_description_template(
                description,
                self.trailer_current_series_folder,
                product_path,
                self.variable_manager
            )
            self.trailer_comment_text.delete("1.0", tk.END)
            self.trailer_comment_text.insert("1.0", processed)

        # 画像枚数
        total = count_total_images_in_character_folders(product_path, min_images=50)
        self.trailer_file_number_var.set(str(total) if total > 0 else "")

        # パロディ詳細
        self.trailer_parody_entries[0].delete(0, tk.END)
        self.trailer_parody_entries[0].insert(0, self.trailer_current_product_folder)

        char_folders = get_character_folders(product_path, min_images=50)
        for i in range(3):
            self.trailer_parody_entries[i+1].delete(0, tk.END)
            if i < len(char_folders):
                self.trailer_parody_entries[i+1].insert(0, char_folders[i])

        # ふりがな生成
        if self.profile_manager.openai_api_key:
            original_title = f"{self.trailer_current_series_folder} {self.trailer_current_product_folder}"
            success, result = self.furigana_converter.convert(original_title.replace("〇", ""))
            if success:
                self.trailer_title_ruby_var.set(result)

        self.status_var.set(f"予約作品読み込み完了: {product_path}")

    def save_trailer_product(self):
        """予約作品データを保存"""
        if not (hasattr(self, 'trailer_current_base_folder') and
                hasattr(self, 'trailer_current_series_folder') and
                hasattr(self, 'trailer_current_product_folder')):
            messagebox.showwarning("警告", "商品フォルダを選択してください")
            return

        if not (self.trailer_current_base_folder and self.trailer_current_series_folder and self.trailer_current_product_folder):
            messagebox.showwarning("警告", "商品フォルダを選択してください")
            return

        # 予約作品用データを保存
        product_path = Path(self.trailer_current_base_folder) / self.trailer_current_series_folder / self.trailer_current_product_folder
        info_path = product_path / "trailer_info.json"

        data = self.get_trailer_data()
        os.makedirs(product_path, exist_ok=True)
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        messagebox.showinfo("保存完了", f"予約作品データを保存しました:\n{info_path}")

    def get_trailer_data(self):
        """予約作品用のデータを取得"""
        data = {
            "title": self.trailer_title_var.get(),
            "title_ruby": self.trailer_title_ruby_var.get(),
            "article_type": self.get_combo_key(ARTICLE_TYPES, self.trailer_article_combo.current()),
            "ai_generated_type": self.get_combo_key(AI_GENERATED_TYPES, self.trailer_ai_combo.current()),
            "section": self.get_combo_key(SECTIONS, self.trailer_section_combo.current()),
            "keyword_age": self.get_combo_key(KEYWORD_AGES, self.trailer_age_combo.current()),
            "comment": self.trailer_comment_text.get("1.0", tk.END).strip(),
            "file_number": self.trailer_file_number_var.get(),
            "keywords": self.trailer_selected_keywords.copy(),
            "parody_names": [e.get() for e in self.trailer_parody_entries],
            "price_undecided": self.trailer_price_undecided_var.get(),
            "price_retail": self.trailer_price_var.get() if not self.trailer_price_undecided_var.get() else "",
            "monopoly_hope_flg": "1" if self.trailer_monopoly_var.get() else "0",
            "drm_hope": self.get_combo_key(DRM_OPTIONS, self.trailer_drm_combo.current()),
            "trailer_release_date_type": self.trailer_release_date_type_var.get(),  # 予告開始日指定
            "release_undecided": self.trailer_release_undecided_var.get(),
            "note": self.trailer_note_text.get("1.0", tk.END).strip(),
        }

        # パロディ
        parody_idx = self.trailer_parody_combo.current()
        if parody_idx > 0:
            data["parody_type"] = PARODY_TYPES[parody_idx - 1][0]
        else:
            data["parody_type"] = ""

        return data

    def reload_trailer_product(self):
        """予約作品データを再読み込み"""
        if hasattr(self, 'trailer_current_product_folder') and self.trailer_current_product_folder:
            self.load_trailer_product_data()
            self.status_var.set("予約作品再読み込み完了")

    def save_trailer_keywords_to_storage(self):
        """予約作品用キーワードをストレージに保存"""
        if not hasattr(self, 'trailer_current_profile_id'):
            messagebox.showwarning("警告", "プロファイルを選択してください")
            return
        if not hasattr(self, 'trailer_current_series_folder') or not self.trailer_current_series_folder:
            messagebox.showwarning("警告", "シリーズを選択してください")
            return

        self.keyword_storage.save_keywords_by_profile_series(
            self.trailer_current_profile_id,
            self.trailer_current_series_folder,
            self.trailer_selected_keywords.copy()
        )
        messagebox.showinfo("保存完了", f"キーワードを保存しました\nシリーズ: {self.trailer_current_series_folder}")

    def start_trailer_auto_input(self):
        """予約作品の自動入力開始"""
        if not hasattr(self, 'trailer_current_profile_id'):
            messagebox.showwarning("警告", "プロファイルを選択してください")
            return

        if self.trailer_parody_combo.current() == 0:
            messagebox.showwarning("警告", "パロディを選択してください")
            return

        data = self.get_trailer_data()

        def run():
            try:
                self.status_var.set("ブラウザを起動中...")
                from browser.form_filler import FanzaTrailerFormFiller

                browser = AdsPowerBrowser(self.ads_api)
                if not browser.start(self.trailer_current_profile_id):
                    self.status_var.set("ブラウザ起動失敗")
                    messagebox.showerror("エラー", "ブラウザの起動に失敗しました")
                    return

                self.status_var.set("予約作品フォーム入力中...")
                filler = FanzaTrailerFormFiller(browser.get_driver(), callback=lambda m: self.status_var.set(m))
                filler.navigate_to_form()
                filler.fill_form(data)

                self.status_var.set("完了！ブラウザで確認してください")
                messagebox.showinfo("完了", "予約作品の自動入力が完了しました。\nブラウザで内容を確認してください。")
            except Exception as e:
                self.status_var.set(f"エラー: {e}")
                messagebox.showerror("エラー", str(e))

        threading.Thread(target=run, daemon=True).start()

    def create_settings_tab(self):
        c = self.COLORS
        # スクロール可能なキャンバス
        canvas = tk.Canvas(self.settings_frame, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === 接続設定 ===
        conn_frame = ttk.LabelFrame(scrollable_frame, text="接続設定", padding=10)
        conn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(conn_frame, text="AdsPower API URL:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.api_url_var = tk.StringVar(value=self.profile_manager.api_url)
        ttk.Entry(conn_frame, textvariable=self.api_url_var, width=40).grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(conn_frame, text="接続テスト", command=self.test_connection).grid(row=0, column=2, padx=5)

        self.conn_status = ttk.Label(conn_frame, text="未接続")
        self.conn_status.grid(row=0, column=3, padx=5)

        ttk.Label(conn_frame, text="OpenAI API Key:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.openai_key_var = tk.StringVar(value=self.profile_manager.openai_api_key)
        ttk.Entry(conn_frame, textvariable=self.openai_key_var, width=40, show="*").grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(conn_frame, text="保存", command=self.save_settings).grid(row=1, column=2, padx=5)

        # === プロファイル設定 ===
        profile_frame = ttk.LabelFrame(scrollable_frame, text="プロファイル設定", padding=10)
        profile_frame.pack(fill=tk.X, padx=5, pady=5)

        # グループ選択
        ttk.Label(profile_frame, text="グループ:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.group_combo = ttk.Combobox(profile_frame, width=30, state="readonly")
        self.group_combo.grid(row=0, column=1, padx=5, pady=2)
        self.group_combo.bind("<<ComboboxSelected>>", self.on_group_change)

        ttk.Button(profile_frame, text="グループ読み込み", command=self.load_groups).grid(row=0, column=2, padx=5)

        # 利用可能プロファイル
        ttk.Label(profile_frame, text="利用可能:").grid(row=1, column=0, sticky=tk.NW, padx=5)
        self.available_listbox = tk.Listbox(
            profile_frame, width=40, height=6,
            bg=c["entry_bg"], fg=c["entry_fg"],
            selectbackground=c["accent"], selectforeground="#ffffff",
            font=("Meiryo UI", 10), relief=tk.FLAT
        )
        self.available_listbox.grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(profile_frame, text="追加 →", command=self.add_profile).grid(row=1, column=2, padx=5)

        # 登録済みプロファイル
        ttk.Label(profile_frame, text="登録済み:").grid(row=2, column=0, sticky=tk.NW, padx=5)
        self.registered_listbox = tk.Listbox(
            profile_frame, width=40, height=6,
            bg=c["entry_bg"], fg=c["entry_fg"],
            selectbackground=c["accent"], selectforeground="#ffffff",
            font=("Meiryo UI", 10), relief=tk.FLAT
        )
        self.registered_listbox.grid(row=2, column=1, padx=5, pady=2)
        ttk.Button(profile_frame, text="削除", command=self.remove_profile).grid(row=2, column=2, padx=5)

        # フォルダ設定
        ttk.Label(profile_frame, text="フォルダ:").grid(row=3, column=0, sticky=tk.NW, padx=5)
        self.folders_listbox = tk.Listbox(
            profile_frame, width=60, height=4,
            bg=c["entry_bg"], fg=c["entry_fg"],
            selectbackground=c["accent"], selectforeground="#ffffff",
            font=("Meiryo UI", 10), relief=tk.FLAT
        )
        self.folders_listbox.grid(row=3, column=1, padx=5, pady=2)

        folder_btn_frame = ttk.Frame(profile_frame)
        folder_btn_frame.grid(row=3, column=2, padx=5)
        ttk.Button(folder_btn_frame, text="フォルダ追加", command=self.add_folder).pack(pady=2)
        ttk.Button(folder_btn_frame, text="フォルダ削除", command=self.remove_folder).pack(pady=2)

        self.registered_listbox.bind("<<ListboxSelect>>", self.on_registered_select)

        # === 変数設定 ===
        var_frame = ttk.LabelFrame(scrollable_frame, text="説明文変数設定", padding=10)
        var_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(var_frame, text="説明文.txt内で [[変数名]] の形式で使用。置換時は2文字目が〇に伏字されます。").grid(row=0, column=0, columnspan=4, sticky=tk.W, padx=5)

        # 変数リスト
        ttk.Label(var_frame, text="登録済み変数:").grid(row=1, column=0, sticky=tk.NW, padx=5)
        self.variables_listbox = tk.Listbox(
            var_frame, width=50, height=6,
            bg=c["entry_bg"], fg=c["entry_fg"],
            selectbackground=c["accent"], selectforeground="#ffffff",
            font=("Meiryo UI", 10), relief=tk.FLAT
        )
        self.variables_listbox.grid(row=1, column=1, columnspan=2, padx=5, pady=2, sticky=tk.W)

        var_btn_frame = ttk.Frame(var_frame)
        var_btn_frame.grid(row=1, column=3, padx=5, sticky=tk.N)
        ttk.Button(var_btn_frame, text="削除", command=self.delete_variable).pack(pady=2)

        # 新規変数追加
        ttk.Label(var_frame, text="新規追加:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)

        add_var_frame = ttk.Frame(var_frame)
        add_var_frame.grid(row=2, column=1, columnspan=3, sticky=tk.W, padx=5, pady=5)

        ttk.Label(add_var_frame, text="変数名:").pack(side=tk.LEFT)
        self.new_var_name = ttk.Entry(add_var_frame, width=15)
        self.new_var_name.pack(side=tk.LEFT, padx=5)

        ttk.Label(add_var_frame, text="タイプ:").pack(side=tk.LEFT)
        self.new_var_type = ttk.Combobox(add_var_frame, width=20, state="readonly")
        self.new_var_type['values'] = ["シリーズ名", "商品名", "キャラクター（画像数検出）", "固定値"]
        self.new_var_type.current(2)
        self.new_var_type.pack(side=tk.LEFT, padx=5)

        ttk.Label(add_var_frame, text="最小画像数:").pack(side=tk.LEFT)
        self.new_var_min_images = ttk.Entry(add_var_frame, width=5)
        self.new_var_min_images.insert(0, "50")
        self.new_var_min_images.pack(side=tk.LEFT, padx=5)

        ttk.Button(add_var_frame, text="追加", command=self.add_variable).pack(side=tk.LEFT, padx=10)

        # 変数リストを初期化
        self.refresh_variables_list()

    def load_initial_data(self):
        """初期データを読み込み"""
        # プロファイルをロード
        profiles = self.profile_manager.profiles
        self.profile_combo['values'] = [p.profile_name for p in profiles]
        self.trailer_profile_combo['values'] = [p.profile_name for p in profiles]

        # 登録済みプロファイルをリストに表示
        self.refresh_registered_list()

        # 接続テスト
        connected, _ = self.ads_api.check_connection()
        if connected:
            self.conn_status.config(text="接続OK", foreground=self.COLORS["success"])
            self.load_groups()

    def refresh_registered_list(self):
        """登録済みプロファイルリストを更新"""
        self.registered_listbox.delete(0, tk.END)
        for p in self.profile_manager.profiles:
            self.registered_listbox.insert(tk.END, f"{p.profile_name} ({len(p.folders)}フォルダ)")

    def on_profile_change(self, event):
        """プロファイル選択時"""
        idx = self.profile_combo.current()
        if idx >= 0:
            profile = self.profile_manager.profiles[idx]
            self.current_profile_id = profile.profile_id
            self.base_folder_combo['values'] = [Path(f).name for f in profile.folders]
            self.base_folder_combo.set('')
            self.series_combo['values'] = []
            self.series_combo.set('')
            self.product_combo['values'] = []
            self.product_combo.set('')

    def on_base_folder_change(self, event):
        """ベースフォルダ選択時"""
        idx = self.profile_combo.current()
        base_idx = self.base_folder_combo.current()
        if idx >= 0 and base_idx >= 0:
            profile = self.profile_manager.profiles[idx]
            self.current_base_folder = profile.folders[base_idx]
            subfolders = get_subfolders(self.current_base_folder)
            self.series_combo['values'] = subfolders
            self.series_combo.set('')
            self.product_combo['values'] = []
            self.product_combo.set('')

            # キーワードをクリア（シリーズ選択時に読み込む）
            self.selected_keywords = []
            self.update_keyword_display()
            self.update_keyword_button_styles()

    def on_series_change(self, event):
        """シリーズ選択時"""
        self.current_series_folder = self.series_combo.get()
        if self.current_base_folder and self.current_series_folder:
            series_path = Path(self.current_base_folder) / self.current_series_folder
            subfolders = get_subfolders(str(series_path))
            self.product_combo['values'] = subfolders
            self.product_combo.set('')

            # プロファイル＋シリーズ名でキーワード・販売情報を読み込み
            if self.current_profile_id:
                saved_keywords = self.keyword_storage.get_keywords_by_profile_series(
                    self.current_profile_id, self.current_series_folder
                )
                self.selected_keywords = saved_keywords.copy() if saved_keywords else []
                self.update_keyword_display()
                self.update_keyword_button_styles()

                sales_info = self.keyword_storage.get_sales_info_by_profile_series(
                    self.current_profile_id, self.current_series_folder
                )
                if sales_info:
                    self.price_var.set(sales_info.get("price_retail", "800") or "800")
                    self.monopoly_var.set(sales_info.get("monopoly_hope_flg", "0") == "1")
                    self.set_combo_by_key(self.drm_combo, DRM_OPTIONS, sales_info.get("drm_hope", "none"))
                else:
                    # 販売情報がない場合はデフォルト値を設定
                    self.price_var.set("800")

    def on_product_change(self, event):
        """商品フォルダ選択時"""
        self.current_product_folder = self.product_combo.get()
        self.load_product_data()

    def load_product_data(self):
        """商品データを読み込み"""
        if not (self.current_base_folder and self.current_series_folder and self.current_product_folder):
            return

        # タイトル自動生成
        masked_name = mask_second_char(self.current_product_folder)
        title = f"{self.current_series_folder} {masked_name}"
        self.title_var.set(title)

        # 商品パス
        product_path = Path(self.current_base_folder) / self.current_series_folder / self.current_product_folder

        # info.json読み込み
        info_path = product_path / "info.json"
        if info_path.exists():
            self.product.load_from_file(str(info_path))
            self.load_data_to_ui()

        # 説明文読み込み
        description = read_description_file(product_path)
        if description:
            processed = process_description_template(
                description,
                self.current_series_folder,
                product_path,
                self.variable_manager
            )
            self.comment_text.delete("1.0", tk.END)
            self.comment_text.insert("1.0", processed)

        # 画像枚数（50枚以上のフォルダのみカウント、サンプル/サムネイル除外）
        total = count_total_images_in_character_folders(product_path, min_images=50)
        self.file_number_var.set(str(total) if total > 0 else "")

        # パロディ詳細
        self.parody_entries[0].delete(0, tk.END)
        self.parody_entries[0].insert(0, self.current_product_folder)

        char_folders = get_character_folders(product_path, min_images=50)
        for i in range(3):
            self.parody_entries[i+1].delete(0, tk.END)
            if i < len(char_folders):
                self.parody_entries[i+1].insert(0, char_folders[i])

        # ふりがな生成
        if self.profile_manager.openai_api_key:
            original_title = f"{self.current_series_folder} {self.current_product_folder}"
            success, result = self.furigana_converter.convert(original_title.replace("〇", ""))
            if success:
                self.title_ruby_var.set(result)

        self.status_var.set(f"読み込み完了: {product_path}")

    def get_combo_key(self, options, index):
        """コンボボックスのインデックスからキー値を取得"""
        if 0 <= index < len(options):
            return options[index][0]
        return ""

    def set_combo_by_key(self, combo, options, key):
        """キー値でコンボボックスを設定"""
        for i, (k, v) in enumerate(options):
            if k == key:
                combo.current(i)
                return
        combo.current(0)

    def load_data_to_ui(self):
        """ProductDataからUIに読み込み"""
        d = self.product.data
        self.title_var.set(d.get("title", ""))
        self.title_ruby_var.set(d.get("title_ruby", ""))
        self.comment_text.delete("1.0", tk.END)
        self.comment_text.insert("1.0", d.get("comment", ""))
        self.price_var.set(d.get("price_retail", "800") or "800")
        self.monopoly_var.set(d.get("monopoly_hope_flg", "0") == "1")
        self.campaign_var.set(d.get("campaign_kibou_flg", True))
        self.coupon_var.set(d.get("is_coupon_usable", True))
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert("1.0", d.get("note", ""))

        # コンボボックス設定
        self.set_combo_by_key(self.article_combo, ARTICLE_TYPES, d.get("article_type", "cg"))
        self.set_combo_by_key(self.ai_combo, AI_GENERATED_TYPES, d.get("ai_generated_type", "2"))
        self.set_combo_by_key(self.section_combo, SECTIONS, d.get("section", "1"))
        self.set_combo_by_key(self.age_combo, KEYWORD_AGES, d.get("keyword_age", "156023"))
        self.set_combo_by_key(self.drm_combo, DRM_OPTIONS, d.get("drm_hope", "none"))
        self.set_combo_by_key(self.auto_join_combo, CAMPAIGN_AUTO_JOIN_OPTIONS, d.get("campaign_auto_join_flg_set_days", "0"))

        # 割引設定
        self.discount_enabled_var.set(d.get("pre_release_articles_campaign_flg", "1") == "1")
        discount_days = d.get("pre_release_articles_campaign_discount_days", "28")
        try:
            days_idx = int(discount_days) - 1
            if 0 <= days_idx < 28:
                self.discount_days_combo.current(days_idx)
        except (ValueError, TypeError):
            self.discount_days_combo.current(27)

        discount_rate = d.get("pre_release_articles_campaign_discount_rate", "80")
        rate_values = ["10", "15", "20", "25", "30", "35", "40", "45", "50", "55", "60", "65", "70", "75", "80", "85", "90", "95"]
        try:
            rate_idx = rate_values.index(discount_rate)
            self.discount_rate_combo.current(rate_idx)
        except ValueError:
            self.discount_rate_combo.current(14)

        # 配信開始日指定
        self.release_date_type_var.set(d.get("release_date_type", "1"))

        # パロディ（オフセット+1 for "(選択してください)")
        parody_key = d.get("parody_type", "")
        if parody_key:
            for i, (k, v) in enumerate(PARODY_TYPES):
                if k == parody_key:
                    self.parody_combo.current(i + 1)
                    break
        else:
            self.parody_combo.current(0)

        self.selected_keywords = d.get("keywords", []).copy()
        self.update_keyword_display()

    def save_ui_to_data(self):
        """UIからProductDataに保存"""
        d = self.product.data
        d["title"] = self.title_var.get()
        d["title_ruby"] = self.title_ruby_var.get()
        d["comment"] = self.comment_text.get("1.0", tk.END).strip()
        d["file_number"] = self.file_number_var.get()
        d["price_retail"] = self.price_var.get()
        d["monopoly_hope_flg"] = "1" if self.monopoly_var.get() else "0"
        d["campaign_kibou_flg"] = self.campaign_var.get()
        d["is_coupon_usable"] = self.coupon_var.get()
        d["note"] = self.note_text.get("1.0", tk.END).strip()
        d["keywords"] = self.selected_keywords.copy()
        d["parody_names"] = [e.get() for e in self.parody_entries]

        # コンボボックスからキー値を取得
        d["article_type"] = self.get_combo_key(ARTICLE_TYPES, self.article_combo.current())
        d["ai_generated_type"] = self.get_combo_key(AI_GENERATED_TYPES, self.ai_combo.current())
        d["section"] = self.get_combo_key(SECTIONS, self.section_combo.current())
        d["keyword_age"] = self.get_combo_key(KEYWORD_AGES, self.age_combo.current())
        d["drm_hope"] = self.get_combo_key(DRM_OPTIONS, self.drm_combo.current())
        d["campaign_auto_join_flg_set_days"] = self.get_combo_key(CAMPAIGN_AUTO_JOIN_OPTIONS, self.auto_join_combo.current())

        # 割引設定
        d["pre_release_articles_campaign_flg"] = "1" if self.discount_enabled_var.get() else "0"
        d["pre_release_articles_campaign_discount_days"] = self.discount_days_combo.get()
        d["pre_release_articles_campaign_discount_rate"] = self.discount_rate_combo.get()

        # 配信開始日指定
        d["release_date_type"] = self.release_date_type_var.get()

        # パロディ（インデックス-1 for "(選択してください)")
        parody_idx = self.parody_combo.current()
        if parody_idx > 0:
            d["parody_type"] = PARODY_TYPES[parody_idx - 1][0]
        else:
            d["parody_type"] = ""

    def save_product(self):
        """商品データを保存"""
        if not (self.current_base_folder and self.current_series_folder and self.current_product_folder):
            messagebox.showwarning("警告", "商品フォルダを選択してください")
            return

        self.save_ui_to_data()
        product_path = Path(self.current_base_folder) / self.current_series_folder / self.current_product_folder
        info_path = product_path / "info.json"
        self.product.save_to_file(str(info_path))
        messagebox.showinfo("保存完了", f"保存しました:\n{info_path}")

    def reload_product(self):
        """商品データを再読み込み"""
        if self.current_product_folder:
            self.load_product_data()
            self.status_var.set("再読み込み完了")

    def resize_comment(self, height):
        """説明文エリアのサイズを変更"""
        self.comment_text.config(height=height)

    def start_auto_input(self):
        """自動入力開始"""
        if not self.current_profile_id:
            messagebox.showwarning("警告", "プロファイルを選択してください")
            return

        if self.parody_combo.current() == 0:
            messagebox.showwarning("警告", "パロディを選択してください")
            return

        if not self.price_var.get():
            messagebox.showwarning("警告", "販売価格を入力してください")
            return

        self.save_ui_to_data()

        def run():
            try:
                self.status_var.set("ブラウザを起動中...")
                from browser.form_filler import FanzaFormFiller

                browser = AdsPowerBrowser(self.ads_api)
                if not browser.start(self.current_profile_id):
                    self.status_var.set("ブラウザ起動失敗")
                    messagebox.showerror("エラー", "ブラウザの起動に失敗しました")
                    return

                self.status_var.set("フォーム入力中...")
                filler = FanzaFormFiller(browser.get_driver(), callback=lambda m: self.status_var.set(m))
                filler.navigate_to_form()
                filler.fill_form(self.product.data)

                self.status_var.set("完了！ブラウザで確認してください")
                messagebox.showinfo("完了", "自動入力が完了しました。\nブラウザで内容を確認してください。")
            except Exception as e:
                self.status_var.set(f"エラー: {e}")
                messagebox.showerror("エラー", str(e))

        threading.Thread(target=run, daemon=True).start()

    def create_keyword_buttons(self, parent, keywords):
        """キーワードボタンを作成（スクロール対応）"""
        c = self.COLORS
        # スクロール可能なキャンバス
        canvas = tk.Canvas(parent, height=180, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        btn_frame = ttk.Frame(canvas)

        btn_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=btn_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # マウスホイールスクロール
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            return "break"

        canvas.bind("<MouseWheel>", on_mousewheel)
        btn_frame.bind("<MouseWheel>", on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ボタンを配置（1行に7個、幅を小さく）
        cols = 7
        for i, item in enumerate(keywords):
            if len(item) == 2:
                kid, name = item
                is_recommend = False
            else:
                kid, name, is_recommend = item

            # 長い名前は短縮（8文字まで）
            display_name = name[:8] + ".." if len(name) > 8 else name

            btn = ttk.Button(
                btn_frame,
                text=display_name,
                width=10,
                style="Keyword.TButton",
                command=lambda k=kid: self.toggle_keyword(k)
            )
            btn.grid(row=i//cols, column=i%cols, padx=1, pady=1, sticky="w")

            # ボタンの参照を保持（同じキーワードが複数タブにある場合に対応）
            if kid not in self.keyword_buttons:
                self.keyword_buttons[kid] = []
            self.keyword_buttons[kid].append(btn)

            # ボタンにもマウスホイールをバインド
            btn.bind("<MouseWheel>", on_mousewheel)

    def toggle_keyword(self, keyword_id):
        """キーワードをトグル（追加/削除）"""
        if keyword_id in self.selected_keywords:
            self.selected_keywords.remove(keyword_id)
        elif len(self.selected_keywords) < 10:
            self.selected_keywords.append(keyword_id)
        self.update_keyword_display()
        self.update_keyword_button_styles()

    def add_keyword(self, keyword_id):
        """キーワードを追加"""
        if keyword_id not in self.selected_keywords and len(self.selected_keywords) < 10:
            self.selected_keywords.append(keyword_id)
            self.update_keyword_display()

    def clear_keywords(self):
        """キーワードをクリア"""
        self.selected_keywords.clear()
        self.update_keyword_display()
        self.update_keyword_button_styles()

    def update_keyword_display(self):
        """キーワード表示を更新"""
        names = [get_keyword_name(k) for k in self.selected_keywords]
        self.keyword_display.config(text=", ".join(names) if names else "(なし)")
        self.keyword_label.config(text=f"選択中: {len(self.selected_keywords)}/10")

    def update_keyword_button_styles(self):
        """キーワードボタンの選択状態を視覚的に更新"""
        for kid, btn_list in self.keyword_buttons.items():
            style = "Selected.TButton" if kid in self.selected_keywords else "Keyword.TButton"
            for btn in btn_list:
                btn.configure(style=style)

    def save_keywords_to_storage(self):
        """キーワードをプロファイル＋シリーズ名でストレージに保存"""
        if not self.current_profile_id:
            messagebox.showwarning("警告", "プロファイルを選択してください")
            return
        if not self.current_series_folder:
            messagebox.showwarning("警告", "シリーズを選択してください")
            return

        self.keyword_storage.save_keywords_by_profile_series(
            self.current_profile_id,
            self.current_series_folder,
            self.selected_keywords.copy()
        )

        # 販売情報も一緒に保存
        sales_info = {
            "price_retail": self.price_var.get(),
            "monopoly_hope_flg": "1" if self.monopoly_var.get() else "0",
            "drm_hope": self.get_combo_key(DRM_OPTIONS, self.drm_combo.current()),
        }
        self.keyword_storage.save_sales_info_by_profile_series(
            self.current_profile_id,
            self.current_series_folder,
            sales_info
        )

        messagebox.showinfo("保存完了", f"キーワードと販売情報を保存しました\nシリーズ: {self.current_series_folder}")

    # === 設定タブのメソッド ===
    def test_connection(self):
        """AdsPower接続テスト"""
        self.ads_api.api_url = self.api_url_var.get()
        connected, msg = self.ads_api.check_connection()
        if connected:
            self.conn_status.config(text="接続OK", foreground=self.COLORS["success"])
            self.load_groups()
        else:
            self.conn_status.config(text=f"失敗: {msg}", foreground=self.COLORS["accent"])

    def save_settings(self):
        """設定を保存"""
        self.profile_manager.api_url = self.api_url_var.get()
        self.profile_manager.openai_api_key = self.openai_key_var.get()
        self.profile_manager.save()
        self.furigana_converter.set_api_key(self.openai_key_var.get())
        messagebox.showinfo("保存", "設定を保存しました")

    def load_groups(self):
        """グループを読み込み"""
        groups = self.ads_api.get_groups()
        self.group_combo['values'] = [g.get("group_name", "") for g in groups]
        self.group_data = groups

    def on_group_change(self, event):
        """グループ選択時"""
        idx = self.group_combo.current()
        if idx >= 0 and hasattr(self, 'group_data'):
            group_id = self.group_data[idx].get("group_id")
            profiles = self.ads_api.get_profiles(group_id)
            self.available_listbox.delete(0, tk.END)
            self.available_profiles = profiles
            for p in profiles:
                self.available_listbox.insert(tk.END, p.get("name", ""))

    def add_profile(self):
        """プロファイルを追加"""
        sel = self.available_listbox.curselection()
        if sel and hasattr(self, 'available_profiles'):
            p = self.available_profiles[sel[0]]
            self.profile_manager.add_profile(p.get("user_id"), p.get("name"))
            self.refresh_registered_list()
            # プロファイルコンボも更新（両タブ）
            profile_names = [p.profile_name for p in self.profile_manager.profiles]
            self.profile_combo['values'] = profile_names
            self.trailer_profile_combo['values'] = profile_names

    def remove_profile(self):
        """プロファイルを削除"""
        sel = self.registered_listbox.curselection()
        if sel:
            profile = self.profile_manager.profiles[sel[0]]
            self.profile_manager.remove_profile(profile.profile_id)
            self.refresh_registered_list()
            # プロファイルコンボも更新（両タブ）
            profile_names = [p.profile_name for p in self.profile_manager.profiles]
            self.profile_combo['values'] = profile_names
            self.trailer_profile_combo['values'] = profile_names

    def on_registered_select(self, event):
        """登録済みプロファイル選択時"""
        sel = self.registered_listbox.curselection()
        if sel:
            profile = self.profile_manager.profiles[sel[0]]
            self.folders_listbox.delete(0, tk.END)
            for f in profile.folders:
                self.folders_listbox.insert(tk.END, f)
            self.selected_profile_for_folder = profile.profile_id

    def add_folder(self):
        """フォルダを追加"""
        if not hasattr(self, 'selected_profile_for_folder'):
            messagebox.showwarning("警告", "先にプロファイルを選択してください")
            return

        folder = filedialog.askdirectory(title="作品フォルダを選択")
        if folder:
            self.profile_manager.add_folder_to_profile(self.selected_profile_for_folder, folder)
            self.refresh_registered_list()
            # フォルダリストも更新
            profile = self.profile_manager.get_profile(self.selected_profile_for_folder)
            if profile:
                self.folders_listbox.delete(0, tk.END)
                for f in profile.folders:
                    self.folders_listbox.insert(tk.END, f)

    def remove_folder(self):
        """フォルダを削除"""
        if not hasattr(self, 'selected_profile_for_folder'):
            return

        sel = self.folders_listbox.curselection()
        if sel:
            folder = self.folders_listbox.get(sel[0])
            self.profile_manager.remove_folder_from_profile(self.selected_profile_for_folder, folder)
            self.folders_listbox.delete(sel[0])
            self.refresh_registered_list()

    def refresh_variables_list(self):
        """変数リストを更新"""
        self.variables_listbox.delete(0, tk.END)
        type_labels = {
            "series": "シリーズ名",
            "product": "商品名",
            "character": "キャラクター",
            "static": "固定値"
        }
        for var in self.variable_manager.variables:
            type_label = type_labels.get(var.var_type, var.var_type)
            detail = ""
            if var.var_type == "character":
                detail = f" ({var.min_images}枚以上)"
            elif var.var_type == "static" and var.value:
                detail = f" (値: {var.value})"
            self.variables_listbox.insert(tk.END, f"[[{var.name}]] - {type_label}{detail}")

    def delete_variable(self):
        """変数を削除"""
        sel = self.variables_listbox.curselection()
        if sel:
            var = self.variable_manager.variables[sel[0]]
            self.variable_manager.remove_variable(var.name)
            self.refresh_variables_list()
            messagebox.showinfo("削除", f"変数 [[{var.name}]] を削除しました")

    def add_variable(self):
        """変数を追加"""
        name = self.new_var_name.get().strip()
        if not name:
            messagebox.showwarning("警告", "変数名を入力してください")
            return

        if self.variable_manager.get_variable(name):
            messagebox.showwarning("警告", "同じ名前の変数が既に存在します")
            return

        type_map = {
            "シリーズ名": "series",
            "商品名": "product",
            "キャラクター（画像数検出）": "character",
            "固定値": "static"
        }
        var_type = type_map.get(self.new_var_type.get(), "character")

        min_images = 50
        try:
            min_images = int(self.new_var_min_images.get())
        except ValueError:
            pass

        new_var = VariableConfig(
            name=name,
            var_type=var_type,
            min_images=min_images,
        )
        self.variable_manager.add_variable(new_var)
        self.refresh_variables_list()

        # フィールドをリセット
        self.new_var_name.delete(0, tk.END)
        messagebox.showinfo("追加", f"変数 [[{name}]] を追加しました")


def main():
    root = tk.Tk()
    app = FanzaAutoInputApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
