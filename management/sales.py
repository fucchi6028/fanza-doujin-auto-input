"""
売上（卸金額）の集計と Discord 通知

FANZA売上ページから取り込んだ日別の卸金額を SQLite(sales_daily) に貯め、
「前日分の合計」「今月分の累計」をアカウント別内訳つきで通知する。

通知先は settings の sales_webhook_url（未設定なら出品管理と同じ
discord_webhook_url）。多重送信は settings のガードキーで防ぐ:
  last_sales_notice_date : 日次通知を送った対象日（'YYYY-MM-DD'）
  last_sales_monthly_ym  : 月次確定通知を送った年月（'YYYY-MM'）
  last_sales_alert_date  : 未取得アラートを送った対象日（'YYYY-MM-DD'）
"""
from datetime import date, timedelta

from management import db
from management import discord_notify
from management.db import account_sort_key, normalize_account

SALES_WEBHOOK_KEY = "sales_webhook_url"
LISTING_WEBHOOK_KEY = "discord_webhook_url"
DAILY_GUARD_KEY = "last_sales_notice_date"
MONTHLY_GUARD_KEY = "last_sales_monthly_ym"
ALERT_GUARD_KEY = "last_sales_alert_date"

USERNAME = "FANZA売上速報"


# ── 日付ユーティリティ ──────────────────────────────────
def yesterday(today: date = None) -> date:
    return (today or date.today()) - timedelta(days=1)


def ym_of(d: date) -> str:
    return d.strftime("%Y-%m")


def prev_ym_of(d: date) -> str:
    first = d.replace(day=1)
    return ym_of(first - timedelta(days=1))


def is_month_start(d: date = None) -> bool:
    return (d or date.today()).day == 1


def yen(n) -> str:
    if n is None:
        return "未取得"
    return f"¥{int(n):,}"


class SalesReporter:
    """売上の記録・集計・通知をまとめたクラス。"""

    def __init__(self, db_path=None, notifier=None):
        self.db_path = db_path
        self._notifier = notifier  # notifier(text)->bool（テスト差し替え用）
        db.init_db(self.db_path)

    # ── 通知 ──────────────────────────────────────────
    def get_webhook(self) -> str:
        url = db.get_setting(SALES_WEBHOOK_KEY, "", self.db_path) or ""
        if not url:
            url = db.get_setting(LISTING_WEBHOOK_KEY, "", self.db_path) or ""
        return url

    def set_webhook(self, url: str):
        db.set_setting(SALES_WEBHOOK_KEY, url or "", self.db_path)

    def notify(self, text: str) -> bool:
        if self._notifier is not None:
            try:
                return bool(self._notifier(text))
            except Exception as e:
                print(f"[sales] notifier failed: {e}")
                return False
        return discord_notify.send(self.get_webhook(), text, username=USERNAME)

    def notify_detailed(self, text: str):
        if self._notifier is not None:
            try:
                return bool(self._notifier(text)), "カスタム通知"
            except Exception as e:
                return False, str(e)
        return discord_notify.send_detailed(self.get_webhook(), text, username=USERNAME)

    # ── 記録 ──────────────────────────────────────────
    def record(self, sale_date, account: str, amount: int):
        """1アカウント・1日分の卸金額を保存（再取得なら上書き）。"""
        if isinstance(sale_date, date):
            sale_date = sale_date.isoformat()
        db.upsert_sales(sale_date, normalize_account(account), amount, self.db_path)

    def record_month(self, ym: str, account: str, amount: int, as_of_date):
        """FANZAの売上ページから取った月累計を保存する。

        日次分の足し算ではなくFANZA側の期間集計をそのまま持つので、途中から
        使い始めた月や、FANZA側で遡って修正された場合でも正しい値になる。
        """
        if isinstance(as_of_date, date):
            as_of_date = as_of_date.isoformat()
        db.upsert_sales_month(ym, normalize_account(account), amount,
                              as_of_date, self.db_path)

    def recorded_accounts(self, sale_date) -> set:
        if isinstance(sale_date, date):
            sale_date = sale_date.isoformat()
        return db.sales_recorded_accounts(sale_date, self.db_path)

    def missing_accounts(self, sale_date, accounts) -> list:
        """その日の取得がまだ済んでいないアカウントコード（リトライ対象）。"""
        done = self.recorded_accounts(sale_date)
        return [a for a in accounts if a not in done]

    # ── 集計 ──────────────────────────────────────────
    def day_amounts(self, sale_date) -> dict:
        if isinstance(sale_date, date):
            sale_date = sale_date.isoformat()
        return db.get_sales_day(sale_date, self.db_path)

    def month_amounts(self, ym: str) -> dict:
        """アカウント別の月累計 {account: amount}。

        FANZAから直接取った月累計（sales_month）を優先し、無いアカウントだけ
        日次データ（sales_daily）の合算でおぎなう。
        """
        amounts = db.get_sales_month(ym, self.db_path)      # 日次の合算
        amounts.update(db.get_sales_month_reported(ym, self.db_path))  # FANZA由来で上書き
        return amounts

    def day_total(self, sale_date) -> int:
        return sum(self.day_amounts(sale_date).values())

    def month_total(self, ym: str) -> int:
        return sum(self.month_amounts(ym).values())

    @staticmethod
    def _label(code: str) -> str:
        """通知の表示ラベル。アカウントコードのみ（サークル名は出さない）。"""
        return code

    # ── 通知文の組み立て ──────────────────────────────
    def daily_report_text(self, sale_date, accounts=None, reasons=None) -> str:
        """前日分＋当月累計をアカウント別内訳つきで組み立てる。

        sale_date が月末日（＝通知日が月初）の場合、「今月分」は sale_date の
        属する月＝前月の累計になる。見出しでその旨を明示する。
        """
        if isinstance(sale_date, str):
            sale_date = date.fromisoformat(sale_date)
        ym = ym_of(sale_date)
        day = self.day_amounts(sale_date)
        month = self.month_amounts(ym)

        targets = list(accounts) if accounts else sorted(
            set(day) | set(month), key=account_sort_key)
        targets = sorted(targets, key=account_sort_key)
        missing = [a for a in targets if a not in day]

        month_label = f"{ym} 累計"
        if is_month_start() and ym != ym_of(date.today()):
            month_label = f"{ym} 累計（前月・確定）"

        lines = [f"💰 売上速報　{sale_date.strftime('%Y/%m/%d')}（前日分）"]
        lines.append(f"■ 前日 卸金額 合計: {yen(sum(day.values()))}")
        lines.append(f"■ {month_label}: {yen(sum(month.values()))}")
        lines.append("── アカウント別（前日 / 累計）")
        for code in targets:
            d = day.get(code)
            m = month.get(code, 0)
            lines.append(f"　{self._label(code)}　{yen(d)} / {yen(m)}")
        if missing:
            lines.append(f"⚠️ 未取得: {'、'.join(missing)}（合計に含まれていません）")
            for code in missing:
                why = (reasons or {}).get(code)
                if why:
                    lines.append(f"　{code}: {why}")
        return "\n".join(lines)

    def incomplete_report_text(self, sale_date, accounts=None, reasons=None) -> str:
        """全件そろわなかったときの途中経過。

        1アカウントのプロキシ不調で全体の通知が昼過ぎまで止まらないよう、
        取れた分の金額は先に知らせる。合計は「未取得分を除く」と明示し、
        全件そろった時点の売上速報（日次ガードつき）であらためて正しい合計を送る。
        """
        if isinstance(sale_date, str):
            sale_date = date.fromisoformat(sale_date)
        day = self.day_amounts(sale_date)
        targets = sorted(list(accounts) if accounts else list(day), key=account_sort_key)
        missing = [a for a in targets if a not in day]
        got = [a for a in targets if a in day]

        lines = [f"⚠️ 売上取得が未完了　{sale_date.strftime('%Y/%m/%d')}（前日分）"]
        lines.append(f"■ 取得済み: {len(got)}/{len(targets)}件")
        if got:
            lines.append(f"■ 取得済み分の合計: {yen(sum(day[a] for a in got))}"
                         "（未取得分を除く）")
            for code in got:
                lines.append(f"　{self._label(code)}　{yen(day[code])}")
        lines.append(f"■ 未取得: {'、'.join(missing)}")
        for code in missing:
            why = (reasons or {}).get(code)
            if why:
                lines.append(f"　{code}: {why}")
        lines.append("※ 未取得分が取れしだい、全件の合計で売上速報を送ります。")
        return "\n".join(lines)

    def monthly_report_text(self, ym: str, accounts=None) -> str:
        """前月確定の月次合計（アカウント別内訳つき）。"""
        month = self.month_amounts(ym)
        days = db.get_sales_days_in_month(ym, self.db_path)
        targets = list(accounts) if accounts else list(month)
        targets = sorted(targets, key=account_sort_key)

        lines = [f"🗓️ {ym} 月次確定　卸金額"]
        lines.append(f"■ 合計: {yen(sum(month.values()))}")
        lines.append(f"■ 集計日数: {len(days)}日分")
        lines.append("── アカウント別")
        for code in targets:
            lines.append(f"　{self._label(code)}　{yen(month.get(code, 0))}")
        return "\n".join(lines)

    def payment_forecast_text(self, ym: str) -> str:
        """確定した月の卸金額から出した振込予想（入金率は過去の振込実績から）。

        予想を出せない（振込実績が無い等）・失敗したときは空文字。月次通知の
        おまけなので、ここで例外を投げて月次通知ごと止めないようにする。
        """
        try:
            from browser.sales_scraper import load_config
            from management import payment
            from management.sales_stats import SalesStats
            summaries, _, _ = payment.summaries_with_forecast(
                load_config(), SalesStats(self.db_path).months, self.db_path)
            summ = summaries.get(ym)
            if summ is None or not summ.is_forecast:
                return ""
            return payment.report_text(summ)
        except Exception as e:
            print(f"[sales] 振込予想を作れませんでした: {e}")
            return ""

    # ── 通知の実行（多重送信ガードつき） ───────────────
    def notify_daily(self, sale_date, accounts=None, force=False, reasons=None) -> bool:
        """前日分を通知。同じ対象日を二重に送らない（force=True で強制）。"""
        if isinstance(sale_date, date):
            sale_date = sale_date.isoformat()
        if not force and db.get_setting(DAILY_GUARD_KEY, "", self.db_path) == sale_date:
            print(f"[sales] {sale_date} は通知済みのためスキップ")
            return False
        ok = self.notify(self.daily_report_text(sale_date, accounts, reasons))
        if ok:
            db.set_setting(DAILY_GUARD_KEY, sale_date, self.db_path)
        return ok

    def notify_incomplete(self, sale_date, accounts=None, reasons=None,
                          force=False) -> bool:
        """未取得のまま打ち切ったことを通知する（対象日ごとに1回だけ）。

        日次通知のガード（last_sales_notice_date）は立てない。全件そろった
        あとの実行で、通常どおり売上速報が送られるようにするため。
        """
        if isinstance(sale_date, date):
            sale_date = sale_date.isoformat()
        if not force and db.get_setting(ALERT_GUARD_KEY, "", self.db_path) == sale_date:
            print(f"[sales] {sale_date} の未取得アラートは送信済み")
            return False
        ok = self.notify(self.incomplete_report_text(sale_date, accounts, reasons))
        if ok:
            db.set_setting(ALERT_GUARD_KEY, sale_date, self.db_path)
        return ok

    def maybe_notify_monthly(self, today: date = None, accounts=None, force=False) -> bool:
        """月初に一度だけ前月確定合計を通知する。

        月末分の売上は翌日（＝月初）に取得されるため、月初の日次取得が
        終わったこのタイミングで前月が確定する。
        """
        today = today or date.today()
        # 月初判定は force でも飛ばさない（月の途中で前月確定を送らないため）。
        # force は「送信済みフラグを無視して再送する」ためだけに使う。
        if not is_month_start(today):
            return False
        pv = prev_ym_of(today)
        if not force and db.get_setting(MONTHLY_GUARD_KEY, "", self.db_path) == pv:
            print(f"[sales] {pv} の月次通知は送信済み")
            return False
        if not self.month_amounts(pv):
            print(f"[sales] {pv} の売上データが無いため月次通知をスキップ")
            return False
        text = self.monthly_report_text(pv, accounts)
        forecast = self.payment_forecast_text(pv)
        if forecast:
            text += "\n\n" + forecast
        ok = self.notify(text)
        if ok:
            db.set_setting(MONTHLY_GUARD_KEY, pv, self.db_path)
        return ok
