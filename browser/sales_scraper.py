"""
FANZA同人「売上情報」ページから卸金額を取り出す

対象ページ: https://dojin.dmm.co.jp/sales/all/catalog/{circle}/{page}/{from}/{to}
    例) /sales/all/catalog/all/0/2026-08-24/2026-08-24   … 8/24単日
        /sales/all/catalog/all/0/2026-08-01/2026-08-24   … 8月の累計

期間がURLに載るので、単日と月累計をそれぞれ直接取得できる。月累計をFANZA側から
そのまま取ることで、途中から使い始めた場合やFANZA側の遡り修正にも追従する。

「まだ未更新」と「本当に0円」の見分け:
    ページ上部に「毎日9時ごろに更新 (2026/08/25 08:10更新)」という更新時刻がある。
    この更新日が対象日の翌日以降なら、対象日の売上は確定済み（表が空＝0円）。
    更新日が古ければ未更新なので、リトライする。
"""
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from selenium.webdriver.common.by import By

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sales_config.json"

DEFAULT_CONFIG = {
    # {device}=all/pc/sp, {circle}=all または circle_id, {from}/{to}=YYYY-MM-DD
    "sales_url": "https://dojin.dmm.co.jp/sales/{device}/catalog/{circle}/0/{from}/{to}",
    "device": "all",       # 総合売上（PC＋スマホ）
    "circle": "all",       # そのアカウントの全サークル合計
    "wait_sec": 5,
    "reload_retry": 2,     # 表が読めなかったときのページ再読み込み回数
}

# 「サークル単位の合計」表のヘッダー
CIRCLE_TABLE_HEADER = "サークル名"
AMOUNT_HEADER = "卸金額合計"
UPDATED_RE = re.compile(r"\((\d{4})/(\d{1,2})/(\d{1,2})\s*(\d{1,2}):(\d{2})\s*更新\)")


class SalesFetchError(RuntimeError):
    """ページ構造が想定と違う等、取得そのものに失敗した場合。"""


class SalesLoginRequired(SalesFetchError):
    """FANZAのログインが切れている（AdsPowerで再ログインが必要）。"""


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            cfg.update({k: v for k, v in user.items() if v not in (None, "")})
        except Exception as e:
            print(f"[sales_scraper] 設定読み込みエラー: {e}")
    return cfg


def build_url(cfg: dict, date_from: date, date_to: date) -> str:
    return (cfg["sales_url"]
            .replace("{device}", cfg.get("device", "all"))
            .replace("{circle}", str(cfg.get("circle", "all")))
            .replace("{from}", date_from.isoformat())
            .replace("{to}", date_to.isoformat()))


def parse_amount(text):
    """'12,345円' '¥12,345' '---' → 12345 / None。"""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    t = t.translate(str.maketrans("０１２３４５６７８９，－", "0123456789,-"))
    m = re.search(r"-?[\d,]+", t)
    if not m:
        return None
    digits = m.group(0).replace(",", "")
    if digits in ("", "-"):
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_updated_at(page_text: str):
    """「(2026/08/25 08:10更新)」→ datetime。見つからなければ None。"""
    m = UPDATED_RE.search(page_text)
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def is_fresh_for(updated_at, target: date) -> bool:
    """対象日の売上が確定しているか。

    対象日の売上は翌日の更新で載る。更新日が対象日の翌日以降なら確定済み。
    """
    if updated_at is None:
        return False
    return updated_at.date() >= target + timedelta(days=1)


AGE_CHECK_MARK = "年齢認証"
LOGIN_MARK = "ログイン - FANZA"


def check_logged_in(driver):
    """ログインページに飛ばされていたら SalesLoginRequired を投げる。"""
    try:
        title = driver.title or ""
    except Exception:
        return
    if LOGIN_MARK in title or title.startswith("ログイン"):
        raise SalesLoginRequired(
            "FANZAのログインが切れています。AdsPowerでこのプロファイルを開き、"
            "「ログインしたままにする」にチェックしてログインし直してください。")


def pass_age_check(driver, report=print) -> bool:
    """年齢認証ページに飛ばされていたら「はい」を通過する。通過したら True。

    プロファイルに年齢確認のCookieが無いと、売上ページの代わりにこの画面が
    出て解析に失敗する。declared=yes のリンクへ移動して同意済みにする。
    """
    try:
        if AGE_CHECK_MARK not in (driver.title or ""):
            return False
    except Exception:
        return False

    report("  年齢認証ページを検出 → 通過します")
    for selector in ('a[href*="declared=yes"]', 'a[href*="age_check"]'):
        for a in driver.find_elements(By.CSS_SELECTOR, selector):
            href = a.get_attribute("href") or ""
            if "declared=yes" in href:
                driver.get(href)
                time.sleep(2)
                return True
    report("  年齢認証の「はい」リンクが見つかりませんでした")
    return False


def _rows_of(table):
    rows = []
    for tr in table.find_elements(By.TAG_NAME, "tr"):
        cells = tr.find_elements(By.TAG_NAME, "th") + tr.find_elements(By.TAG_NAME, "td")
        if cells:
            rows.append([(c.text or "").strip() for c in cells])
    return rows


def extract_circle_total(driver):
    """「サークル単位の合計」表から (卸金額合計, 行数) を返す。

    複数サークルを持つアカウントでは全行を合計する。行が無ければ (0, 0)。
    表そのものが見つからない場合は SalesFetchError。
    """
    for table in driver.find_elements(By.CSS_SELECTOR, "table.list01"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = rows[0]
        if not any(CIRCLE_TABLE_HEADER in c for c in header):
            continue  # 作品単位の表なのでスキップ
        col = next((i for i, c in enumerate(header) if AMOUNT_HEADER in c), None)
        if col is None:
            raise SalesFetchError(f"『{AMOUNT_HEADER}』列が見つかりません: {header}")
        total, count = 0, 0
        for row in rows[1:]:
            if col >= len(row):
                continue
            amount = parse_amount(row[col])
            if amount is not None:
                total += amount
                count += 1
        return total, count
    raise SalesFetchError("『サークル単位の合計』表が見つかりません（ログアウト等の可能性）")


def fetch_range(driver, date_from: date, date_to: date, cfg: dict = None, report=print):
    """指定期間の卸金額合計を取得する。

    戻り値 dict:
        amount     … 卸金額合計（int。行が無ければ0）
        rows       … サークル行数（0なら売上なし）
        updated_at … ページの更新日時（datetime / None）
        url        … 実際に開いたURL
    """
    cfg = cfg or load_config()
    url = build_url(cfg, date_from, date_to)
    report(f"  取得: {url}")

    last_error = None
    for attempt in range(int(cfg.get("reload_retry", 2)) + 1):
        driver.get(url)
        time.sleep(cfg.get("wait_sec", 5))
        if pass_age_check(driver, report):
            driver.get(url)  # 通過後に本来のページを開き直す
            time.sleep(cfg.get("wait_sec", 5))
        check_logged_in(driver)  # ログイン切れはリトライしても無駄なので即中断
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
        except Exception as e:
            body = ""
            last_error = e
        try:
            amount, rows = extract_circle_total(driver)
        except SalesFetchError as e:
            last_error = e
            report(f"  表の解析に失敗（{attempt + 1}回目）: {e}")
            continue
        updated_at = parse_updated_at(body)
        report(f"  → 卸金額 {amount:,}円 / {rows}行 / 更新 {updated_at}")
        return {"amount": amount, "rows": rows, "updated_at": updated_at, "url": url}

    raise SalesFetchError(str(last_error) or "取得に失敗しました")


def fetch_daily_and_month(driver, target: date, cfg: dict = None, report=print):
    """対象日の単日と、対象日が属する月の累計をまとめて取得する。

    月初に実行した場合、対象日＝前月末日なので月累計＝前月の確定合計になる。

    戻り値 dict:
        fresh      … 対象日の売上が確定しているか（False ならリトライすべき）
        day        … 対象日の卸金額（fresh のときだけ意味を持つ）
        month      … 対象日の属する月の累計卸金額
        ym         … 対象日の年月 'YYYY-MM'
        updated_at … ページの更新日時
    """
    cfg = cfg or load_config()
    day = fetch_range(driver, target, target, cfg, report)
    updated_at = day["updated_at"]
    fresh = is_fresh_for(updated_at, target)
    if not fresh:
        report(f"  未更新: ページの更新は {updated_at} / {target} 分はまだ反映されていません")
        return {"fresh": False, "day": None, "month": None,
                "ym": target.strftime("%Y-%m"), "updated_at": updated_at}

    month_start = target.replace(day=1)
    month = fetch_range(driver, month_start, target, cfg, report)
    return {
        "fresh": True,
        "day": day["amount"],
        "month": month["amount"],
        "ym": target.strftime("%Y-%m"),
        "updated_at": updated_at,
    }


# ── 支払情報の確認（実際の振込額） ──────────────────────
# 表の列: 振込日 / 詳細（「08月分の支払通知書」リンク） / 支払結果 / 支払金額合計 / 次月繰越
PAYMENT_URL = "https://dojin.dmm.co.jp/payment_notification"
PAYMENT_TABLE_HEADER = "振込日"
PAYMENT_LINK_RE = re.compile(r"/payment_notification/(\d{4})(\d{2})")
PAY_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
MONTH_OF_RE = re.compile(r"(\d{1,2})\s*月分")


def _sales_ym_of(pay_date: date, detail_href: str, detail_text: str):
    """支払行が「何月分の売上」かを 'YYYY-MM' で返す。

    詳細リンク /payment_notification/202608 が一番確実。リンクが無い行は
    「08月分」の表記と振込日から推定する（振込日の前月〜それ以前）。
    """
    m = PAYMENT_LINK_RE.search(detail_href or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = MONTH_OF_RE.search(detail_text or "")
    if m and pay_date:
        month = int(m.group(1))
        year = pay_date.year if month < pay_date.month else pay_date.year - 1
        return f"{year}-{month:02d}"
    return None


def extract_payments(driver) -> list:
    """「支払情報の確認」の表を読み、支払行の一覧を返す。

    各行 dict: sales_ym / pay_date / status / amount / carry_over
    表が無ければ SalesFetchError（支払実績ゼロの場合も表のヘッダーは出る）。
    """
    for table in driver.find_elements(By.CSS_SELECTOR, "table.list01"):
        trs = table.find_elements(By.TAG_NAME, "tr")
        if not trs:
            continue
        header = [(c.text or "").strip() for c in trs[0].find_elements(By.TAG_NAME, "th")]
        if not header or PAYMENT_TABLE_HEADER not in header[0]:
            continue
        col = {name: i for i, name in enumerate(header)}
        need = ("振込日", "詳細", "支払結果", "支払金額合計")
        if any(n not in col for n in need):
            raise SalesFetchError(f"支払情報の表の列が想定と違います: {header}")

        payments = []
        for tr in trs[1:]:
            tds = tr.find_elements(By.TAG_NAME, "td")
            if len(tds) < len(header):
                continue
            texts = [(td.text or "").strip() for td in tds]
            m = PAY_DATE_RE.search(texts[col["振込日"]])
            if not m:
                continue
            pay_date = date(*(int(x) for x in m.groups()))
            href = ""
            for a in tds[col["詳細"]].find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href") or ""
                break
            sales_ym = _sales_ym_of(pay_date, href, texts[col["詳細"]])
            if not sales_ym:
                continue
            carry = parse_amount(texts[col["次月繰越"]]) if "次月繰越" in col else 0
            payments.append({
                "sales_ym": sales_ym,
                "pay_date": pay_date,
                "status": texts[col["支払結果"]],
                "amount": parse_amount(texts[col["支払金額合計"]]) or 0,
                "carry_over": carry or 0,
            })
        return payments
    raise SalesFetchError("『支払情報の確認』の表が見つかりません（ログアウト等の可能性）")


def fetch_payments(driver, cfg: dict = None, report=print, year: int = None) -> list:
    """支払情報の確認ページを開いて支払行の一覧を返す（extract_payments 参照）。

    year を指定するとその年の一覧（既定は FANZA 側の既定＝今年）。
    """
    cfg = cfg or load_config()
    url = PAYMENT_URL + (f"?year={year}" if year else "")
    report(f"  取得: {url}")
    last_error = None
    for attempt in range(int(cfg.get("reload_retry", 2)) + 1):
        driver.get(url)
        time.sleep(cfg.get("wait_sec", 5))
        if pass_age_check(driver, report):
            driver.get(url)
            time.sleep(cfg.get("wait_sec", 5))
        check_logged_in(driver)
        try:
            payments = extract_payments(driver)
        except SalesFetchError as e:
            last_error = e
            report(f"  表の解析に失敗（{attempt + 1}回目）: {e}")
            continue
        for p in payments:
            report(f"  → {p['sales_ym']}分 振込日 {p['pay_date']} {p['status']} "
                   f"{p['amount']:,}円（繰越 {p['carry_over']:,}円）")
        if not payments:
            report("  → 支払行はまだありません")
        return payments
    raise SalesFetchError(str(last_error) or "取得に失敗しました")


def dump_page(driver, path: Path):
    """解析に失敗したときの調査用にページを保存する。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(driver.page_source, encoding="utf-8")
        print(f"  ページを保存: {path}")
    except Exception as e:
        print(f"  ページ保存に失敗: {e}")
