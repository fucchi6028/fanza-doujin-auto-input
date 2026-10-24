"""振込（実際の支払額）の集計 ― 支払日の計算と「2割引き」の集計

FANZA同人の売上（卸金額）は月末締め・翌月20日払い。20日が土日祝なら
次の平日に振り込まれる（例: 2026年8月分 → 9/20(日)・21〜23が祝日 → 9/24）。
振込額は卸金額そのままではなく、手数料等が引かれた金額になるため、
実額は sales_payment.py が「支払情報の確認」ページから取り込む。

画面・通知では、アカウントごとの振込額に加えて「振込額から2割引いた金額」
（分配用）も出す。2割引きの対象から外すアカウント（既定は 002）と割合は
config/sales_config.json の payment_share_exclude / payment_share_rate で変えられる。
"""
from datetime import date, timedelta

from management import db
from management.db import account_sort_key

try:
    import jpholiday
except ImportError:  # 未インストールなら土日だけで判定する（祝日はずれる）
    jpholiday = None

PAY_DAY = 20
DEFAULT_SHARE_RATE = 0.8         # 振込額の8割（＝2割引いた金額）
DEFAULT_SHARE_EXCLUDE = ("002",)
DEFAULT_SPLIT = 2                # 2割引き合計を何人で分けるか（1人あたりの取り分）
DEFAULT_MIN_PAYOUT = 3000        # これ未満（卸金額）は振り込まれず次月繰越になる


def is_business_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if jpholiday is not None and jpholiday.is_holiday(d):
        return False
    return True


def next_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def pay_date_for(sales_ym: str) -> date:
    """売上月 'YYYY-MM' の振込予定日（翌月20日、休日なら次の平日）。"""
    ny = next_ym(sales_ym)
    d = date(int(ny[:4]), int(ny[5:7]), PAY_DAY)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def share_settings(cfg: dict = None):
    """(割合, 除外アカウントの集合) を設定から読む。"""
    cfg = cfg or {}
    try:
        rate = float(cfg.get("payment_share_rate", DEFAULT_SHARE_RATE))
    except (TypeError, ValueError):
        rate = DEFAULT_SHARE_RATE
    exclude = cfg.get("payment_share_exclude", DEFAULT_SHARE_EXCLUDE)
    return rate, {str(x) for x in (exclude or [])}


def group_settings(cfg: dict = None) -> list:
    """別枠で合計を出すアカウントのまとまり [(名前, [アカウント…])]。

    config/sales_config.json の payment_groups（例: 004〜007）。
    """
    groups = []
    for g in (cfg or {}).get("payment_groups") or []:
        codes = [str(a) for a in (g.get("accounts") or [])]
        if codes:
            groups.append((str(g.get("name") or "・".join(codes)), codes))
    return groups


def person_deduct_settings(cfg: dict = None):
    """「1人あたりの取り分から特定アカウントの取り分を引いた額」の設定 (表示名, アカウント)。

    config/sales_config.json の payment_person_deduct（例: 1人あたり − 003の取り分）。
    未設定なら None。
    """
    d = (cfg or {}).get("payment_person_deduct") or {}
    account = str(d.get("account") or "")
    if not account:
        return None
    return str(d.get("name") or f"1人あたり − {account}"), account


def share_of(amount: int, rate: float) -> int:
    """振込額から2割引いた金額（円未満切り捨て）。"""
    return int(int(amount) * rate // 1)


class PaymentSummary:
    """1ヶ月分（売上月単位）の振込実績の集計結果。"""

    def __init__(self, sales_ym: str, rows: list, cfg: dict = None):
        self.sales_ym = sales_ym
        self.rate, self.exclude = share_settings(cfg)
        try:
            self.split = max(1, int((cfg or {}).get("payment_split", DEFAULT_SPLIT)))
        except (TypeError, ValueError):
            self.split = DEFAULT_SPLIT
        self.rows = {r["account"]: r for r in rows}
        self.accounts = sorted(self.rows, key=account_sort_key)
        self.groups = group_settings(cfg)
        self.person_deduct = person_deduct_settings(cfg)
        # 振込予想（forecast_summary が作ったもの）なら True。forecast_rate は使った入金率
        self.is_forecast = False
        self.forecast_rate = None

    def amount(self, account: str) -> int:
        return int(self.rows.get(account, {}).get("amount") or 0)

    def share(self, account: str):
        """2割引いた金額。除外アカウントは None。"""
        if account in self.exclude or account not in self.rows:
            return None
        return share_of(self.amount(account), self.rate)

    def diff(self, account: str):
        """差引（振込額 − 2割引いた金額）。除外アカウントは None。"""
        share = self.share(account)
        return None if share is None else self.amount(account) - share

    @property
    def diff_total(self) -> int:
        return sum(self.diff(a) or 0 for a in self.accounts)

    @property
    def total(self) -> int:
        return sum(self.amount(a) for a in self.accounts)

    @property
    def share_total(self) -> int:
        return sum(self.share(a) or 0 for a in self.accounts)

    @property
    def per_person(self) -> int:
        """1人あたりの取り分（2割引き合計 ÷ 人数、円未満切り捨て）。"""
        return self.share_total // self.split

    @property
    def deducted_person(self):
        """1人あたりの取り分 − 指定アカウントの取り分（2割引いた残金）。

        未設定なら None。指定アカウントが未取得・対象外のときも None。
        """
        if not self.person_deduct:
            return None
        share = self.share(self.person_deduct[1])
        return None if share is None else self.per_person - share

    def group_totals(self, codes) -> dict:
        """指定アカウントだけの 振込額・2割引き・差引 の合計（取得済みの分のみ）。"""
        got = [a for a in codes if a in self.rows]
        return {
            "total": sum(self.amount(a) for a in got),
            "share_total": sum(self.share(a) or 0 for a in got),
            "diff_total": sum(self.diff(a) or 0 for a in got),
            "missing": [a for a in codes if a not in self.rows],
        }

    @property
    def pay_date(self) -> str:
        dates = sorted({r.get("pay_date") or "" for r in self.rows.values()} - {""})
        return dates[-1] if dates else pay_date_for(self.sales_ym).isoformat()

    @property
    def carry_total(self) -> int:
        return sum(int(r.get("carry_over") or 0) for r in self.rows.values())


def load_summary(sales_ym: str, cfg: dict = None, db_path=None) -> PaymentSummary:
    return PaymentSummary(sales_ym, db.get_sales_payments(sales_ym, db_path), cfg)


def load_all(cfg: dict = None, db_path=None) -> dict:
    """{売上月: PaymentSummary}（振込実績がある月だけ）。"""
    by_ym = {}
    for r in db.get_sales_payments(None, db_path):
        by_ym.setdefault(r["sales_ym"], []).append(r)
    return {ym: PaymentSummary(ym, rows, cfg) for ym, rows in sorted(by_ym.items())}


# ── 振込予想 ──────────────────────────────────────────
# 入金額は卸金額から手数料等が引かれた額で、実績では全アカウントほぼ同じ率
# （約89.8%）になる。確定済みの月から「入金額 ÷ 卸金額」を求め、まだ振込の
# 無い月はその率で入金額を予想する。前月から繰り越された卸金額は当月分に
# 足して振り込まれるので、率の計算・予想とも繰越分を含めて扱う。

def min_payout(cfg: dict = None) -> int:
    try:
        return int((cfg or {}).get("payment_min_payout", DEFAULT_MIN_PAYOUT))
    except (TypeError, ValueError):
        return DEFAULT_MIN_PAYOUT


def _carry_in(prev: "PaymentSummary", code: str) -> int:
    if prev is None or code not in prev.rows:
        return 0
    return int(prev.rows[code].get("carry_over") or 0)


def estimate_rate(confirmed: dict, months: dict, skip=()):
    """確定済みの振込から入金率（入金額 ÷ 卸金額）を求める。

    confirmed: {売上月: PaymentSummary}、months: {売上月: {アカウント: 卸金額}}。
    繰越で0円だった行は除く。戻り値 (率 or None, 計算に使った売上月のリスト)。
    """
    paid = base = 0
    used = []
    for ym, summ in sorted(confirmed.items()):
        if ym in skip:
            continue
        sales = months.get(ym) or {}
        prev = confirmed.get(prev_ym(ym))
        n = 0
        for code in summ.accounts:
            amount = summ.amount(code)
            b = int(sales.get(code) or 0) + _carry_in(prev, code)
            if amount <= 0 or b <= 0:
                continue
            paid += amount
            base += b
            n += 1
        if n:
            used.append(ym)
    return (paid / base if base else None), used


def forecast_summary(sales_ym: str, month_sales: dict, cfg: dict, rate: float,
                     prev: "PaymentSummary" = None) -> PaymentSummary:
    """卸金額と入金率から、その月の振込を予想した PaymentSummary を作る。"""
    minimum = min_payout(cfg)
    pay = pay_date_for(sales_ym).isoformat()
    rows = []
    for code, sales in month_sales.items():
        base = int(sales or 0) + _carry_in(prev, code)
        if base <= 0:
            continue
        if base < minimum:
            rows.append({"account": code, "amount": 0, "carry_over": base,
                         "pay_date": pay, "status": "次月繰越（予想）"})
        else:
            rows.append({"account": code, "amount": int(base * rate + 0.5),
                         "carry_over": 0, "pay_date": pay, "status": "予想"})
    summ = PaymentSummary(sales_ym, rows, cfg)
    summ.is_forecast = True
    summ.forecast_rate = rate
    return summ


def summaries_with_forecast(cfg: dict, months: dict, db_path=None):
    """確定済みの月はそのまま、振込がまだの月は予想を入れた {売上月: PaymentSummary}。

    戻り値 (summaries, 入金率 or None, 率の計算に使った売上月)。
    """
    confirmed = load_all(cfg, db_path)
    rate, basis = estimate_rate(confirmed, months)
    out = {}
    for ym in sorted(set(months) | set(confirmed)):
        if ym in confirmed:
            out[ym] = confirmed[ym]
        elif rate is not None and months.get(ym):
            out[ym] = forecast_summary(ym, months[ym], cfg, rate, out.get(prev_ym(ym)))
    return out, rate, basis


def forecast_before_confirm(sales_ym: str, cfg: dict, months: dict, summaries: dict):
    """確定済みの月について「確定前に出していた予想」を作り直す（予想との差の表示用）。

    その月自身を除いた実績から率を求める。ほかに実績が無ければ None。
    """
    confirmed = {ym: s for ym, s in summaries.items() if not s.is_forecast}
    rate, _ = estimate_rate(confirmed, months, skip={sales_ym})
    if rate is None or not months.get(sales_ym):
        return None
    return forecast_summary(sales_ym, months[sales_ym], cfg, rate,
                            summaries.get(prev_ym(sales_ym)))


def report_text(summary: PaymentSummary, sales_amounts: dict = None,
                missing=None, forecast: PaymentSummary = None) -> str:
    """Discord 用の振込レポート（入金額・2割引いた残金・アカウント主に支払う金額）。"""
    ym = summary.sales_ym
    ex = "、".join(sorted(summary.exclude, key=account_sort_key))
    if summary.is_forecast:
        lines = [f"🔮 振込予想　{ym[:4]}/{ym[5:7]}分　振込予定日 {summary.pay_date.replace('-', '/')}",
                 f"※ これまでの実績から、卸金額の{summary.forecast_rate * 100:.1f}%が入金される計算です"
                 "（振込を確認できたら確定額を通知します）"]
    else:
        lines = [f"🏦 振込確認　{ym[:4]}/{ym[5:7]}分　振込日 {summary.pay_date.replace('-', '/')}"]
    lines.append(f"■ 入金額 合計: ¥{summary.total:,}")
    if forecast is not None and not summary.is_forecast:
        d = summary.total - forecast.total
        lines.append(f"　（予想 ¥{forecast.total:,} → 確定 ¥{summary.total:,}　差 {d:+,}円）")
    lines.append(f"■ 2割引いた残金（自分達の取り分）合計（{ex}除く）: ¥{summary.share_total:,}")
    lines.append(f"■ 1人あたりの取り分: ¥{summary.per_person:,}")
    if summary.person_deduct:
        name, acc = summary.person_deduct
        v = summary.deducted_person
        lines.append(f"■ {name}（1人あたり − {acc}の取り分）: "
                     + ("未取得" if v is None else f"¥{v:,}"))
    lines.append(f"■ アカウント主に支払う金額 合計: ¥{summary.diff_total:,}")
    for name, codes in summary.groups:
        g = summary.group_totals(codes)
        lines.append(f"── {name}（入金額 / 自分達の取り分 / アカウント主に支払う金額）")
        lines.append(f"　合計　¥{g['total']:,} / ¥{g['share_total']:,} / ¥{g['diff_total']:,}")
    lines.append("── アカウント別（入金額 / 自分達の取り分 / アカウント主に支払う金額）")
    for code in summary.accounts:
        share, diff = summary.share(code), summary.diff(code)
        carry = int(summary.rows[code].get("carry_over") or 0)
        if not summary.amount(code) and carry:
            lines.append(f"　{code}　¥0（次月繰越 ¥{carry:,}）")
            continue
        tail = "対象外" if share is None else f"¥{share:,} / ¥{diff:,}"
        lines.append(f"　{code}　¥{summary.amount(code):,} / {tail}")
    if summary.carry_total:
        lines.append(f"※ 次月繰越あり: ¥{summary.carry_total:,}")
    if missing:
        lines.append(f"⚠️ 未取得: {'、'.join(missing)}（合計に含まれていません）")
    return "\n".join(lines)
