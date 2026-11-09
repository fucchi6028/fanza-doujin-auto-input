"""売上（卸金額）の集計 ― 売上管理アプリ(sales_app.py)用の読み取り専用レイヤ

sales_daily（日別）と sales_month（FANZAの売上ページから取った月累計）から、
アカウント別・月別・日別の集計を組み立てる。月合計の決め方は
management.sales.SalesReporter と同じで、FANZA由来の月累計を正とし、
その月・そのアカウントに無いぶんだけ日次の合算でおぎなう
（日次を単純に足すと、途中から取り始めた月やFANZA側の遡り修正でズレるため）。

アカウントの表示名（001（名前）など）は config/sales_accounts.json で持つ。
accounts.name は FANZA上のサークル名なので、そちらとは別管理にしている。
"""
import json
from pathlib import Path

from management import db
from management.db import account_sort_key

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sales_accounts.json"

# 表示名はここに書かない（このリポジトリは公開のため）。
# 名前は gitignore してある config/sales_accounts.json だけに置き、
# 読めなかったときはアカウントコードのみで表示する。
DEFAULT_LABELS = {}


# ── 表示名 ──────────────────────────────────────────────
def load_labels(path=None) -> dict:
    """{'001': '名前', ...} を返す。読めなければ既定値。"""
    try:
        data = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
        labels = {str(k): str(v) for k, v in (data.get("labels") or {}).items()}
        return labels or dict(DEFAULT_LABELS)
    except Exception:
        return dict(DEFAULT_LABELS)


def display_label(code: str, labels=None) -> str:
    """'001（名前）' の形。名前が無いアカウントはコードだけ返す。"""
    name = (labels or {}).get(code, "")
    return f"{code}（{name}）" if name else code


def short_label(code: str, labels=None) -> str:
    """'001 名前' の形。表の列見出しなど幅が狭いところ用。"""
    name = (labels or {}).get(code, "")
    return f"{code} {name}" if name else code


# ── 年月ユーティリティ ──────────────────────────────────
def prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def ym_label(ym: str) -> str:
    return f"{ym[:4]}/{ym[5:7]}" if ym else "―"


def date_label(d: str) -> str:
    return f"{d[5:7]}/{d[8:10]}" if d else "―"


def yen(n) -> str:
    if n is None:
        return "―"
    return f"¥{int(n):,}"


class SalesStats:
    """DBの売上を丸ごと読み込んで、画面が欲しい形に集計して返す。

    対象は1日1行×アカウント数しかない小さなテーブルなので、都度SQLを投げず
    reload() でまとめて読み、あとはメモリ上で集計する。
    """

    def __init__(self, db_path=None, labels=None):
        self.db_path = db_path
        self.labels = dict(labels) if labels is not None else load_labels()
        self.reload()

    # ── 読み込み ──────────────────────────────────────
    def reload(self):
        self.labels = load_labels()
        with db.connect(self.db_path) as conn:
            daily_rows = conn.execute(
                "SELECT sale_date, account, amount FROM sales_daily"
            ).fetchall()
            month_rows = conn.execute(
                "SELECT ym, account, amount, as_of_date FROM sales_month"
            ).fetchall()

        # 日別 {'YYYY-MM-DD': {account: amount}}
        self.daily = {}
        for r in daily_rows:
            self.daily.setdefault(r["sale_date"], {})[r["account"]] = r["amount"]

        # 月別 {'YYYY-MM': {account: amount}} ― まず日次を合算し、
        # FANZA由来の月累計があるアカウントだけ上書きする
        months = {}
        for d, per_account in self.daily.items():
            bucket = months.setdefault(d[:7], {})
            for acc, amount in per_account.items():
                bucket[acc] = bucket.get(acc, 0) + amount
        self.as_of = {}
        for r in month_rows:
            months.setdefault(r["ym"], {})[r["account"]] = r["amount"]
            prev = self.as_of.get(r["ym"], "")
            if (r["as_of_date"] or "") > prev:
                self.as_of[r["ym"]] = r["as_of_date"] or ""
        self.months = months

        known = set(self.labels)
        for per_account in self.months.values():
            known |= set(per_account)
        for per_account in self.daily.values():
            known |= set(per_account)
        self.accounts = sorted(known, key=account_sort_key)
        self.month_list = sorted(self.months)
        self.date_list = sorted(self.daily)

    # ── 日別 ──────────────────────────────────────────
    def day_amounts(self, sale_date: str) -> dict:
        return dict(self.daily.get(sale_date, {}))

    def day_total(self, sale_date: str) -> int:
        return sum(self.daily.get(sale_date, {}).values())

    def days_of_month(self, ym: str) -> list:
        return [d for d in self.date_list if d.startswith(ym)]

    def day_totals(self) -> dict:
        return {d: sum(v.values()) for d, v in sorted(self.daily.items())}

    def latest_date(self) -> str:
        return self.date_list[-1] if self.date_list else ""

    # ── 月別 ──────────────────────────────────────────
    def month_amounts(self, ym: str) -> dict:
        return dict(self.months.get(ym, {}))

    def month_total(self, ym: str) -> int:
        return sum(self.months.get(ym, {}).values())

    def month_totals(self) -> dict:
        return {ym: self.month_total(ym) for ym in self.month_list}

    def latest_ym(self) -> str:
        return self.month_list[-1] if self.month_list else ""

    def as_of_date(self, ym: str) -> str:
        """その月の集計が「いつまで」のものか（FANZA月累計の取得時点）。"""
        return self.as_of.get(ym, "")

    # ── 累計 ──────────────────────────────────────────
    def account_total(self, account: str) -> int:
        return sum(per.get(account, 0) for per in self.months.values())

    def totals_by_account(self) -> dict:
        return {acc: self.account_total(acc) for acc in self.accounts}

    def grand_total(self) -> int:
        return sum(self.month_total(ym) for ym in self.month_list)

    def account_series(self, account: str) -> dict:
        """1アカウントの月別推移 {'YYYY-MM': amount}。"""
        return {ym: self.months.get(ym, {}).get(account, 0) for ym in self.month_list}

    # ── 表示補助 ──────────────────────────────────────
    def label(self, account: str) -> str:
        return display_label(account, self.labels)

    def short(self, account: str) -> str:
        return short_label(account, self.labels)

    def day_average(self, ym: str) -> int:
        """その月の1日あたり平均（記録がある日数で割る）。"""
        days = self.days_of_month(ym)
        if not days:
            return 0
        return round(self.month_total(ym) / len(days))

    def mom_ratio(self, ym: str, account=None):
        """前月比（%）。前月が0/無しなら None。"""
        pv = prev_ym(ym)
        if account:
            base = self.months.get(pv, {}).get(account, 0)
            now = self.months.get(ym, {}).get(account, 0)
        else:
            base = self.month_total(pv)
            now = self.month_total(ym)
        if not base:
            return None
        return (now - base) / base * 100
