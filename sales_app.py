"""FANZA売上管理アプリ（アカウント別の売上ダッシュボード）

sales_daily.py が毎朝DBに貯めた卸金額を、アカウント別・日別・月別に表示する。
このアプリはDBを読むだけで、FANZAからの取得もDiscord通知もしない
（取得と通知は今までどおり sales_daily.py の担当）。

画面:
  アカウント別 : 当月 / 前月 / 前月比 / 日平均 / 構成比 / 累計
  日別         : 選んだ月の1日ごとの金額（アカウント別＋合計）
  月別推移     : 月ごとの金額（アカウント別＋合計）と今までの累計
  振込         : アカウント別の入金額・2割引いた残金（自分達の取り分）・アカウント主に支払う金額（002除く）
                 ※振込額の取得は sales_payment.py（振込日の16時ごろ自動実行）

起動: sales_app.bat  または  python sales_app.py
"""
import csv
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from browser.sales_scraper import load_config as load_sales_config
from management import payment
from management.sales_stats import SalesStats, date_label, prev_ym, yen, ym_label

FONT = "Meiryo UI"
AUTO_RELOAD_MS = 10 * 60 * 1000   # 朝の自動取得を拾うため10分ごとに読み直す
WEEKDAYS = "月火水木金土日"

# ダークテーマ（main_tk.py と同じ配色）
COLORS = {
    "bg": "#1a1a2e",
    "bg_secondary": "#16213e",
    "bg_frame": "#0f3460",
    "fg": "#eaeaea",
    "fg_secondary": "#a0a0a0",
    "accent": "#e94560",
    "accent_hover": "#ff6b6b",
    "button_bg": "#0f3460",
    "button_fg": "#ffffff",
    "entry_bg": "#16213e",
    "entry_fg": "#ffffff",
    "select_bg": "#e94560",
    "border": "#0f3460",
    "success": "#00d9a0",
    "warning": "#ffc107",
    "grid": "#2a3a5e",
}

# アカウントごとの色（グラフの塗り分け用に順番に使う）
SERIES_COLORS = [
    "#e94560", "#00d9a0", "#ffc107", "#4d96ff", "#b980f0",
    "#ff8c42", "#2ec4b6", "#f06595", "#74c0fc", "#a9e34b",
]


def series_color(index: int) -> str:
    return SERIES_COLORS[index % len(SERIES_COLORS)]


def pct(value) -> str:
    return "―" if value is None else f"{value:+.1f}%"


# ── グラフ描画（Canvasに直接描く。外部ライブラリは使わない） ──────────
def _empty(canvas, text="データがありません"):
    canvas.delete("all")
    canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2,
                       text=text, fill=COLORS["fg_secondary"], font=(FONT, 10))


def draw_hbars(canvas, items):
    """横棒グラフ。items: [(ラベル, 金額, 色)]"""
    canvas.delete("all")
    w, h = canvas.winfo_width(), canvas.winfo_height()
    if w < 80 or h < 40:
        return
    if not items or not any(v for _, v, _ in items):
        _empty(canvas)
        return

    pad_l, pad_r, pad_t, pad_b = 150, 110, 12, 8
    rows = len(items)
    row_h = (h - pad_t - pad_b) / rows
    bar_h = min(24, max(6, row_h * 0.6))
    top = max(v for _, v, _ in items) or 1
    span = max(w - pad_l - pad_r, 10)

    for i, (label, value, color) in enumerate(items):
        cy = pad_t + row_h * (i + 0.5)
        canvas.create_text(pad_l - 10, cy, text=label, anchor="e",
                           fill=COLORS["fg"], font=(FONT, 9))
        bar_w = span * (value / top)
        canvas.create_rectangle(pad_l, cy - bar_h / 2,
                                pad_l + max(bar_w, 2), cy + bar_h / 2,
                                fill=color, outline="")
        canvas.create_text(pad_l + max(bar_w, 2) + 8, cy, text=yen(value), anchor="w",
                           fill=COLORS["fg_secondary"], font=(FONT, 9))


def draw_vbars(canvas, items, color, show_average=True):
    """縦棒グラフ。items: [(ラベル, 金額)]。最後の1本（＝最新）だけ色を変える。"""
    canvas.delete("all")
    w, h = canvas.winfo_width(), canvas.winfo_height()
    if w < 80 or h < 60:
        return
    if not items or not any(v for _, v in items):
        _empty(canvas)
        return

    # 平均線のラベルはグラフの外（右）に置く。棒に重ねると読めなくなるため
    pad_l, pad_r, pad_t, pad_b = 78, (86 if show_average else 16), 22, 26
    plot_w = max(w - pad_l - pad_r, 10)
    plot_h = max(h - pad_t - pad_b, 10)
    top = max(v for _, v in items) or 1
    slot = plot_w / len(items)
    bar_w = min(30, max(3, slot * 0.62))
    base_y = pad_t + plot_h

    # 目盛り（0 / 中央 / 最大）
    for frac in (0, 0.5, 1.0):
        y = base_y - plot_h * frac
        canvas.create_line(pad_l, y, pad_l + plot_w, y, fill=COLORS["grid"])
        canvas.create_text(pad_l - 8, y, text=yen(round(top * frac)), anchor="e",
                           fill=COLORS["fg_secondary"], font=(FONT, 8))

    if show_average:
        avg = sum(v for _, v in items) / len(items)
        y = base_y - plot_h * (avg / top)
        canvas.create_line(pad_l, y, pad_l + plot_w, y,
                           fill=COLORS["warning"], dash=(4, 3))
        canvas.create_text(pad_l + plot_w + 6, y, text=f"平均\n{yen(round(avg))}",
                           anchor="w", justify="center",
                           fill=COLORS["warning"], font=(FONT, 8))

    # ラベルが詰まりすぎないよう間引く
    step = max(1, round(len(items) / max(1, plot_w // 46)))
    for i, (label, value) in enumerate(items):
        cx = pad_l + slot * (i + 0.5)
        bar_h = plot_h * (value / top)
        fill = COLORS["accent_hover"] if i == len(items) - 1 else color
        canvas.create_rectangle(cx - bar_w / 2, base_y - max(bar_h, 1),
                                cx + bar_w / 2, base_y, fill=fill, outline="")
        if i % step == 0 or i == len(items) - 1:
            canvas.create_text(cx, base_y + 12, text=label, anchor="n",
                               fill=COLORS["fg_secondary"], font=(FONT, 8))


class SalesApp:
    def __init__(self, root):
        self.root = root
        root.title("FANZA売上管理")
        # ノートPCでも下が切れないよう、画面に収まる範囲で開く
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(1320, screen_w - 80)
        height = min(880, screen_h - 120)
        root.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 0)}+20")
        root.minsize(min(1000, width), min(680, height))

        self.stats = SalesStats()
        self.ym_var = tk.StringVar()
        self.setup_theme()
        self.build_ui()
        self.refresh()
        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.after(AUTO_RELOAD_MS, self.auto_reload)

    # ── テーマ ────────────────────────────────────────
    def setup_theme(self):
        c = COLORS
        self.root.configure(bg=c["bg"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=c["bg"], foreground=c["fg"],
                        fieldbackground=c["entry_bg"], font=(FONT, 10))
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("Title.TLabel", background=c["bg"], foreground=c["accent"],
                        font=(FONT, 16, "bold"))
        style.configure("Sub.TLabel", background=c["bg"], foreground=c["fg_secondary"],
                        font=(FONT, 9))
        style.configure("Section.TLabel", background=c["bg"], foreground=c["fg"],
                        font=(FONT, 10, "bold"))
        style.configure("TButton", background=c["button_bg"], foreground=c["button_fg"],
                        borderwidth=0, focuscolor=c["bg"], font=(FONT, 10))
        style.map("TButton", background=[("active", c["accent"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff",
                        borderwidth=0, font=(FONT, 10, "bold"))
        style.map("Accent.TButton", background=[("active", c["accent_hover"])])
        style.configure("TCombobox", fieldbackground=c["entry_bg"],
                        background=c["button_bg"], foreground=c["entry_fg"],
                        arrowcolor=c["fg"], borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", c["entry_bg"])],
                  selectbackground=[("readonly", c["entry_bg"])],
                  selectforeground=[("readonly", c["entry_fg"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["bg_secondary"], foreground=c["fg"],
                        padding=[18, 8], font=(FONT, 10))
        style.map("TNotebook.Tab", background=[("selected", c["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview", background=c["bg_secondary"],
                        fieldbackground=c["bg_secondary"], foreground=c["fg"],
                        rowheight=26, borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", background=c["bg_frame"], foreground=c["fg"],
                        relief="flat", font=(FONT, 10, "bold"))
        style.map("Treeview.Heading", background=[("active", c["accent"])])
        style.map("Treeview", background=[("selected", c["select_bg"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TScrollbar", background=c["bg_secondary"], troughcolor=c["bg"],
                        arrowcolor=c["fg"], borderwidth=0)
        style.map("TScrollbar", background=[("active", c["accent"])])

    # ── 画面の組み立て ────────────────────────────────
    def build_ui(self):
        c = COLORS
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=16, pady=(14, 6))

        left = ttk.Frame(header)
        left.pack(side=tk.LEFT)
        ttk.Label(left, text="💰 売上管理", style="Title.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(left, text="", style="Sub.TLabel")
        self.subtitle.pack(anchor="w")

        right = ttk.Frame(header)
        right.pack(side=tk.RIGHT)
        ttk.Label(right, text="対象月", style="Sub.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.ym_combo = ttk.Combobox(right, textvariable=self.ym_var, width=10,
                                     state="readonly", font=(FONT, 10))
        self.ym_combo.pack(side=tk.LEFT)
        self.ym_combo.bind("<<ComboboxSelected>>", lambda e: self.render())
        ttk.Button(right, text="🔄 更新", command=self.refresh,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(right, text="⬇ CSV出力", command=self.export_csv).pack(
            side=tk.LEFT, padx=(6, 0))
        self.publish_btn = ttk.Button(right, text="📤 Web公開", command=self.publish_web)
        self.publish_btn.pack(side=tk.LEFT, padx=(6, 0))

        kpi_frame = ttk.Frame(self.root)
        kpi_frame.pack(fill=tk.X, padx=16, pady=8)
        self.kpi = {}
        for key, title, color in (
            ("total", "今までの累計", c["success"]),
            ("month", "当月合計", c["accent"]),
            ("day", "最新日の合計", c["warning"]),
            ("avg", "当月の日平均", "#4d96ff"),
        ):
            self.kpi[key] = self._make_card(kpi_frame, title, color)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=(6, 6))
        self._build_account_tab()
        self._build_day_tab()
        self._build_month_tab()
        self._build_payment_tab()

        self.status = ttk.Label(self.root, text="", style="Sub.TLabel")
        self.status.pack(fill=tk.X, padx=18, pady=(0, 10))

    def _make_card(self, parent, title, color):
        c = COLORS
        card = tk.Frame(parent, bg=c["bg_secondary"], highlightthickness=1,
                        highlightbackground=c["bg_frame"])
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        tk.Frame(card, bg=color, height=3).pack(fill=tk.X)
        tk.Label(card, text=title, bg=c["bg_secondary"], fg=c["fg_secondary"],
                 font=(FONT, 9)).pack(anchor="w", padx=14, pady=(10, 0))
        value = tk.Label(card, text="―", bg=c["bg_secondary"], fg=color,
                         font=(FONT, 20, "bold"))
        value.pack(anchor="w", padx=14)
        note = tk.Label(card, text="", bg=c["bg_secondary"], fg=c["fg_secondary"],
                        font=(FONT, 9))
        note.pack(anchor="w", padx=14, pady=(0, 10))
        return {"value": value, "note": note}

    def _make_tree(self, parent, height):
        wrap = ttk.Frame(parent)
        wrap.pack(fill=tk.X, padx=10, pady=(10, 0))
        tree = ttk.Treeview(wrap, show="headings", height=height)
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 横スクロールバーは列が幅に収まらないときだけ出す（専用の枠に入れて
        # おき、出し入れしても他のウィジェットの並び順が崩れないようにする）
        hwrap = ttk.Frame(parent)
        hwrap.pack(fill=tk.X, padx=10, pady=(0, 4))
        hbar = ttk.Scrollbar(hwrap, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=lambda first, last: self._sync_hbar(hbar, first, last))

        tree.tag_configure("total", background=COLORS["bg_frame"], foreground="#ffffff")
        tree.tag_configure("zero", foreground=COLORS["fg_secondary"])
        return tree

    @staticmethod
    def _sync_hbar(bar, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            bar.pack_forget()
        else:
            bar.pack(fill=tk.X)
        bar.set(first, last)

    def _make_chart(self, parent, title, drawer):
        ttk.Label(parent, text=title, style="Section.TLabel").pack(
            anchor="w", padx=12, pady=(6, 2))
        canvas = tk.Canvas(parent, bg=COLORS["bg_secondary"], highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        canvas.bind("<Configure>", lambda e: drawer())
        return canvas

    @staticmethod
    def _fit_height(tree, cap):
        """表の高さを行数に合わせる（余白をグラフに回すため、上限つき）。"""
        tree.configure(height=max(3, min(len(tree.get_children("")), cap)))

    @staticmethod
    def _set_columns(tree, specs):
        """specs: [(列ID, 見出し, 幅, 寄せ)] で列を作り直す。"""
        tree.configure(columns=[s[0] for s in specs])
        for col_id, text, width, anchor in specs:
            tree.heading(col_id, text=text)
            tree.column(col_id, width=width, minwidth=60, anchor=anchor, stretch=False)

    def _build_account_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  📊 アカウント別  ")
        self.acc_tree = self._make_tree(frame, height=9)
        self.acc_canvas = self._make_chart(frame, "当月のアカウント別 売上",
                                           self.draw_account_chart)

    def _build_day_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  📅 日別  ")
        self.day_tree = self._make_tree(frame, height=13)
        self.day_canvas = self._make_chart(frame, "日ごとの合計（全アカウント）",
                                           self.draw_day_chart)

    def _build_month_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  🗓 月別推移  ")
        self.mon_tree = self._make_tree(frame, height=12)
        self.mon_canvas = self._make_chart(frame, "月ごとの合計（全アカウント）",
                                           self.draw_month_chart)

    def _build_payment_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  🏦 振込  ")
        self.pay_title = ttk.Label(frame, text="", style="Section.TLabel")
        self.pay_title.pack(anchor="w", padx=12, pady=(10, 0))
        self.pay_tree = self._make_tree(frame, height=10)
        self.pay_group_tree = self._make_tree(frame, height=2)
        ttk.Label(frame, text="月ごとの振込実績", style="Section.TLabel").pack(
            anchor="w", padx=12, pady=(10, 0))
        self.pay_hist_tree = self._make_tree(frame, height=6)
        ttk.Label(frame, text="入金額は FANZA「支払情報の確認」から取得（振込日の16時ごろ自動）。"
                              "アカウント主に支払う金額 ＝ 入金額の2割。",
                  style="Sub.TLabel").pack(anchor="w", padx=12, pady=(6, 0))

    # ── データ更新 ────────────────────────────────────
    def auto_reload(self):
        self.refresh(quiet=True)
        self.root.after(AUTO_RELOAD_MS, self.auto_reload)

    def refresh(self, quiet=False):
        try:
            self.stats.reload()
        except Exception as e:
            if not quiet:
                messagebox.showerror("読み込みエラー",
                                     f"売上データを読めませんでした。\n{e}")
            return
        months = self.stats.month_list or [date.today().strftime("%Y-%m")]
        self.ym_combo["values"] = list(reversed(months))
        if self.ym_var.get() not in months:
            self.ym_var.set(months[-1])
        self.render()

    def render(self):
        ym = self.ym_var.get() or date.today().strftime("%Y-%m")
        self.update_kpi(ym)
        self.fill_account_tree(ym)
        self.fill_day_tree(ym)
        self.fill_month_tree()
        self.fill_payment_trees(ym)
        self.draw_account_chart()
        self.draw_day_chart()
        self.draw_month_chart()

        latest = self.stats.latest_date()
        span = (f"{self.stats.date_list[0]} 〜 {latest}"
                if self.stats.date_list else "データなし")
        self.subtitle.config(text=f"卸金額（サークル取り分）　集計期間: {span}")
        self.status.config(
            text=f"最終取得日: {latest or '―'}　/　対象アカウント {len(self.stats.accounts)}件"
                 f"　/　読み込み {datetime.now():%H:%M:%S}　（F5で更新）")

    def update_kpi(self, ym):
        stats = self.stats
        latest = stats.latest_date()
        days = stats.days_of_month(ym)

        self.kpi["total"]["value"].config(text=yen(stats.grand_total()))
        self.kpi["total"]["note"].config(text=f"{len(stats.month_list)}ヶ月分の合計")

        self.kpi["month"]["value"].config(text=yen(stats.month_total(ym)))
        self.kpi["month"]["note"].config(
            text=f"{ym_label(ym)}　前月比 {pct(stats.mom_ratio(ym))}")

        self.kpi["day"]["value"].config(
            text=yen(stats.day_total(latest)) if latest else "―")
        self.kpi["day"]["note"].config(text=f"{latest or '―'} 分")

        self.kpi["avg"]["value"].config(text=yen(stats.day_average(ym)))
        self.kpi["avg"]["note"].config(text=f"{len(days)}日分から算出")

    # ── アカウント別タブ ──────────────────────────────
    def fill_account_tree(self, ym):
        stats = self.stats
        pv = prev_ym(ym)
        self._set_columns(self.acc_tree, [
            ("account", "アカウント", 190, "w"),
            ("month", ym_label(ym), 120, "e"),
            ("prev", ym_label(pv), 120, "e"),
            ("mom", "前月比", 100, "e"),
            ("avg", "日平均", 110, "e"),
            ("share", "構成比", 100, "e"),
            ("total", "累計", 140, "e"),
        ])
        self.acc_tree.delete(*self.acc_tree.get_children())

        month = stats.month_amounts(ym)
        prev = stats.month_amounts(pv)
        month_total = stats.month_total(ym)
        days = max(len(stats.days_of_month(ym)), 1)

        for code in stats.accounts:
            amount = month.get(code, 0)
            share = (amount / month_total * 100) if month_total else 0
            self.acc_tree.insert("", "end", values=(
                stats.label(code),
                yen(amount),
                yen(prev.get(code, 0)),
                pct(stats.mom_ratio(ym, code)),
                yen(round(amount / days)),
                f"{share:.1f}%",
                yen(stats.account_total(code)),
            ), tags=() if amount else ("zero",))

        self.acc_tree.insert("", "end", values=(
            "合計",
            yen(month_total),
            yen(stats.month_total(pv)),
            pct(stats.mom_ratio(ym)),
            yen(stats.day_average(ym)),
            "100.0%" if month_total else "―",
            yen(stats.grand_total()),
        ), tags=("total",))
        self._fit_height(self.acc_tree, 12)

    def draw_account_chart(self):
        ym = self.ym_var.get()
        if not ym:
            return
        month = self.stats.month_amounts(ym)
        items = [(self.stats.label(code), month.get(code, 0), series_color(i))
                 for i, code in enumerate(self.stats.accounts)]
        draw_hbars(self.acc_canvas, items)

    # ── 日別タブ ──────────────────────────────────────
    def fill_day_tree(self, ym):
        stats = self.stats
        specs = [("date", "日付", 120, "center")]
        specs += [(f"a{code}", stats.short(code), 128, "e") for code in stats.accounts]
        specs.append(("total", "合計", 130, "e"))
        self._set_columns(self.day_tree, specs)
        self.day_tree.delete(*self.day_tree.get_children())

        days = stats.days_of_month(ym)
        for d in days:
            per = stats.day_amounts(d)
            weekday = WEEKDAYS[date.fromisoformat(d).weekday()]
            row = [f"{d[5:7]}/{d[8:10]}（{weekday}）"]
            row += [yen(per.get(code, 0)) for code in stats.accounts]
            row.append(yen(sum(per.values())))
            self.day_tree.insert("", "end", values=row)

        if days:
            totals = {code: sum(stats.day_amounts(d).get(code, 0) for d in days)
                      for code in stats.accounts}
            self.day_tree.insert("", "end", values=(
                f"合計（{len(days)}日）",
                *[yen(totals[code]) for code in stats.accounts],
                yen(sum(totals.values())),
            ), tags=("total",))
            self.day_tree.insert("", "end", values=(
                "1日平均",
                *[yen(round(totals[code] / len(days))) for code in stats.accounts],
                yen(round(sum(totals.values()) / len(days))),
            ), tags=("total",))
            # 最新日が見えるよう末尾までスクロールする
            self.day_tree.see(self.day_tree.get_children()[-1])
        self._fit_height(self.day_tree, 10)

    def draw_day_chart(self):
        ym = self.ym_var.get()
        if not ym:
            return
        items = [(date_label(d), self.stats.day_total(d))
                 for d in self.stats.days_of_month(ym)]
        draw_vbars(self.day_canvas, items, COLORS["accent"])

    # ── 月別推移タブ ──────────────────────────────────
    def fill_month_tree(self):
        stats = self.stats
        specs = [("ym", "年月", 110, "center")]
        specs += [(f"a{code}", stats.short(code), 128, "e") for code in stats.accounts]
        specs += [("total", "月合計", 130, "e"), ("mom", "前月比", 100, "e"),
                  ("cum", "累計", 140, "e")]
        self._set_columns(self.mon_tree, specs)
        self.mon_tree.delete(*self.mon_tree.get_children())

        running = 0
        for ym in stats.month_list:
            per = stats.month_amounts(ym)
            running += stats.month_total(ym)
            self.mon_tree.insert("", "end", values=(
                ym_label(ym),
                *[yen(per.get(code, 0)) for code in stats.accounts],
                yen(stats.month_total(ym)),
                pct(stats.mom_ratio(ym)),
                yen(running),
            ))

        self.mon_tree.insert("", "end", values=(
            "累計",
            *[yen(stats.account_total(code)) for code in stats.accounts],
            yen(stats.grand_total()),
            "―",
            yen(stats.grand_total()),
        ), tags=("total",))
        self._fit_height(self.mon_tree, 10)

    def draw_month_chart(self):
        items = [(ym_label(ym), self.stats.month_total(ym))
                 for ym in self.stats.month_list]
        draw_vbars(self.mon_canvas, items, COLORS["success"], show_average=False)

    # ── 振込タブ ──────────────────────────────────────
    def fill_payment_trees(self, ym):
        """選んだ月（売上月）の振込額・2割引き・差引と、月ごとの実績を表示する。

        差引 ＝ 振込額 − 2割引いた金額。2割引きの対象外アカウント（002）は ―。
        """
        cfg = load_sales_config()
        # 振込がまだの月は、これまでの入金率からの予想（確定したら実額に置き換わる）
        summaries, pay_rate, _ = payment.summaries_with_forecast(cfg, self.stats.months)
        summary = summaries.get(ym) or payment.PaymentSummary(ym, [], cfg)
        fc = summary.is_forecast
        excluded = "・".join(sorted(summary.exclude))
        share_head = f"自分達の取り分（{excluded}除く）" if excluded else "自分達の取り分"

        deduct_text = ""
        if summary.person_deduct:
            v = summary.deducted_person
            deduct_text = (f"　／　{summary.person_deduct[0]} "
                           f"{'未取得' if v is None else yen(v)}"
                           f"（1人あたり − {summary.person_deduct[1]}の取り分）")
        if summary.accounts and fc:
            done = self.stats.latest_date() >= f"{payment.next_ym(ym)}-01"
            self.pay_title.config(
                text=f"{ym_label(ym)}分の振込【{'予想' if done else '途中経過の予想'}】"
                     f"　振込予定日 {summary.pay_date.replace('-', '/')}"
                     f"　卸金額の{summary.forecast_rate * 100:.1f}%で計算"
                     f"　／　1人あたりの取り分 {yen(summary.per_person)}" + deduct_text)
        elif summary.accounts:
            before = payment.forecast_before_confirm(ym, cfg, self.stats.months, summaries)
            diff_text = (f"　（予想 {yen(before.total)} との差 {summary.total - before.total:+,}円）"
                         if before else "")
            self.pay_title.config(
                text=f"{ym_label(ym)}分の振込【確定】　振込日 {summary.pay_date.replace('-', '/')}"
                     f"{diff_text}"
                     f"　／　1人あたりの取り分 {yen(summary.per_person)}"
                     f"（自分達の取り分の半分）" + deduct_text)
        else:
            due = payment.pay_date_for(ym)
            self.pay_title.config(
                text=f"{ym_label(ym)}分の振込　振込予定日 {due:%Y/%m/%d}（未取得）")

        self._set_columns(self.pay_tree, [
            ("account", "アカウント", 190, "w"),
            ("paid", "入金額（予想）" if fc else "入金額", 130, "e"),
            ("share", share_head, 170, "e"),
            ("diff", "アカウント主に支払う", 160, "e"),
            ("status", "支払結果", 150, "center"),
        ])
        self.pay_tree.delete(*self.pay_tree.get_children())
        codes = sorted(set(summary.accounts) | set(self.stats.month_amounts(ym)),
                       key=lambda c: self.stats.accounts.index(c)
                       if c in self.stats.accounts else 999)
        for code in codes:
            got = code in summary.rows
            share, diff = summary.share(code), summary.diff(code)
            status = summary.rows.get(code, {}).get("status", "") or "―"
            carry = int(summary.rows.get(code, {}).get("carry_over") or 0)
            if carry:
                status = (f"次月繰越 {yen(carry)}（予想）" if fc
                          else f"{status}（繰越 {yen(carry)}）")
            self.pay_tree.insert("", "end", values=(
                self.stats.label(code),
                yen(summary.amount(code)) if got else "未取得",
                yen(share) if share is not None else ("対象外" if got else "―"),
                yen(diff) if diff is not None else "―",
                status,
            ), tags=() if got else ("zero",))
        self.pay_tree.insert("", "end", values=(
            "合計",
            yen(summary.total) if summary.accounts else "―",
            yen(summary.share_total) if summary.accounts else "―",
            yen(summary.diff_total) if summary.accounts else "―",
            ("予想" if fc else f"{len(summary.accounts)}件取得") if summary.accounts else "",
        ), tags=("total",))
        self._fit_height(self.pay_tree, 12)

        # 004〜007 など、別枠で出すアカウントのまとまりの合計
        self._set_columns(self.pay_group_tree, [
            ("account", "別枠の合計", 190, "w"),
            ("paid", "入金額 合計", 130, "e"),
            ("share", "自分達の取り分 合計", 170, "e"),
            ("diff", "アカウント主に支払う 合計", 180, "e"),
            ("status", "", 150, "center"),
        ])
        self.pay_group_tree.delete(*self.pay_group_tree.get_children())
        for name, codes in summary.groups:
            g = summary.group_totals(codes)
            got = bool(summary.accounts)
            self.pay_group_tree.insert("", "end", values=(
                name,
                yen(g["total"]) if got else "―",
                yen(g["share_total"]) if got else "―",
                yen(g["diff_total"]) if got else "―",
                f"未取得 {'・'.join(g['missing'])}" if got and g["missing"] else "",
            ), tags=("total",))
        self._fit_height(self.pay_group_tree, 4)

        self._set_columns(self.pay_hist_tree, [
            ("ym", "売上月", 110, "center"),
            ("date", "振込日", 120, "center"),
            ("paid", "入金額 合計", 140, "e"),
            ("share", share_head + " 合計", 200, "e"),
            ("diff", "アカウント主に支払う 合計", 180, "e"),
            ("person", "1人あたり", 130, "e"),
            ("kind", "", 90, "center"),
        ])
        self.pay_hist_tree.delete(*self.pay_hist_tree.get_children())
        for s_ym, summ in summaries.items():
            self.pay_hist_tree.insert("", "end", values=(
                ym_label(s_ym), summ.pay_date.replace("-", "/"),
                yen(summ.total), yen(summ.share_total), yen(summ.diff_total),
                yen(summ.per_person),
                "予想" if summ.is_forecast else "確定",
            ))
        conf = [x for x in summaries.values() if not x.is_forecast]
        if conf:
            self.pay_hist_tree.insert("", "end", values=(
                "確定分の累計", "",
                yen(sum(x.total for x in conf)),
                yen(sum(x.share_total for x in conf)),
                yen(sum(x.diff_total for x in conf)),
                yen(sum(x.per_person for x in conf)),
                "",
            ), tags=("total",))
        self._fit_height(self.pay_hist_tree, 8)

    # ── CSV出力 ───────────────────────────────────────
    def export_csv(self):
        index = self.notebook.index(self.notebook.select())
        tree, name = [(self.acc_tree, "アカウント別"),
                      (self.day_tree, "日別"),
                      (self.mon_tree, "月別推移"),
                      (self.pay_tree, "振込")][index]
        path = filedialog.asksaveasfilename(
            title="CSVとして保存", defaultextension=".csv",
            initialfile=f"売上_{name}_{self.ym_var.get()}.csv",
            filetypes=[("CSVファイル", "*.csv")])
        if not path:
            return
        columns = tree["columns"]
        try:
            # Excelで開けるようBOM付きUTF-8。金額は計算できるよう数値のまま書く
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([tree.heading(col)["text"] for col in columns])
                for iid in tree.get_children(""):
                    writer.writerow([self._plain(v) for v in tree.item(iid, "values")])
        except Exception as e:
            messagebox.showerror("保存エラー", f"CSVを保存できませんでした。\n{e}")
            return
        messagebox.showinfo("CSV出力", f"保存しました。\n{path}")

    # ── スマホ共有ページの更新 ───────────────────────
    def publish_web(self):
        """sales_publish.py を呼んで、共有ページを今のデータで更新する。

        毎朝の自動取得後にも同じ処理が走るので、ここは「今すぐ反映したい」
        ときのためのボタン。
        """
        if not messagebox.askyesno(
                "Web公開",
                "いまの売上データで共有ページ（スマホ用）を更新します。\n"
                "共有相手にも即座に反映されます。よろしいですか？"):
            return
        self.publish_btn.config(state="disabled")
        self.status.config(text="共有ページを更新しています…")

        def work():
            try:
                proc = subprocess.run(
                    [sys.executable, str(BASE / "sales_publish.py")],
                    cwd=str(BASE), capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                output = (proc.stdout or "") + (proc.stderr or "")
                tail = "\n".join(output.strip().splitlines()[-4:])
                self.root.after(0, self._publish_done, proc.returncode == 0, tail)
            except Exception as e:
                self.root.after(0, self._publish_done, False, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _publish_done(self, ok, detail):
        self.publish_btn.config(state="normal")
        self.render()
        if ok:
            messagebox.showinfo("Web公開", "共有ページを更新しました。\n\n" + detail)
        else:
            messagebox.showerror(
                "Web公開",
                "更新できませんでした。\n\n" + detail +
                "\n\n設定は config/sales_web.json、記録は logs/sales_publish.log です。")

    @staticmethod
    def _plain(value):
        text = str(value)
        if text.startswith("¥"):
            return text[1:].replace(",", "")
        return text


def main():
    root = tk.Tk()
    SalesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
