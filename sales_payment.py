"""
実際の振込額の取得（Windowsタスクスケジューラから毎日16:00に実行）

FANZA同人の売上は月末締め・翌月20日（休日なら次の平日）払い。振込額は
卸金額から手数料等が引かれるので、「支払情報の確認」ページ
（https://dojin.dmm.co.jp/payment_notification）から実額を取り込む。

処理の流れ:
  1. 前月分の振込予定日（management.payment.pay_date_for）を計算
     ・今日が振込日より前 → 何もせず終了（毎日起動されるので普段はここで終わる）
     ・振込日に16:00より前に起動された → 16:00まで待つ
  2. 各アカウントで支払情報ページを開き、前月分の行を記録
  3. まだ載っていないアカウントは30分ごとに再取得（既定 23:30 まで）
     打ち切ったら翌日16:00の実行で続きを取る（振込日から7日間）
  4. 全アカウントそろったら Discord に振込レポートを送る
  5. スマホ用の共有ページ（sales_publish.py）を更新する

使い方:
  python sales_payment.py                    # 自動（振込日だけ動く）
  python sales_payment.py --ym 2026-08 --now # 8月分をいますぐ取得
  python sales_payment.py --ym 2026-08 --now --profile FANZA-001 --dry-run

終了コード: 0=取得完了 or 対象日でない / 1=一部未取得 / 2=エラー
"""
import argparse
import contextlib
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import sales_daily
from sales_daily import (fetch_one, keep_awake, load_profiles, log, sleep_until)
from browser import sales_scraper
from browser.adspower import AdsPowerAPI
from management import db, payment
from management.db import normalize_account
from management.sales import SalesReporter

# ログは売上集計とは別ファイルに書く（sales_daily.log の関数をそのまま使う）
sales_daily.LOG_PATH = BASE / "logs" / "sales_payment.log"

NOTICE_GUARD_KEY = "last_payment_notice_ym"   # 振込レポートを送った売上月
ALERT_GUARD_KEY = "last_payment_alert_date"   # 未取得アラートを送った日


def hhmm_today(text: str, default: str) -> datetime:
    try:
        h, m = (int(x) for x in str(text or default).split(":"))
    except Exception:
        h, m = (int(x) for x in default.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def fetch_account(api, profile, sales_ym, cfg, month_sales):
    """1アカウントの振込額を取得。戻り値 (row or None, 理由)。

    ページにその月の行がまだ無ければ None（未反映）。ただしその月の売上が
    0円のアカウントは振込自体が発生しないので、0円として確定する。
    """
    pay = payment.pay_date_for(sales_ym)
    res = fetch_one(api, profile, pay, cfg, job=lambda driver: {
        "payments": sales_scraper.fetch_payments(driver, cfg, report=log,
                                                 year=pay.year)})
    if res.get("error"):
        return None, res["error"]
    rows = [p for p in res["payments"] if p["sales_ym"] == sales_ym]
    if rows:
        return rows[0], ""
    code = normalize_account(profile.get("profile_name", ""))
    if code in month_sales and not month_sales[code]:
        return {"sales_ym": sales_ym, "pay_date": pay, "status": "売上なし",
                "amount": 0, "carry_over": 0}, ""
    return None, "支払情報にまだ載っていません"


def run_round(api, profiles, sales_ym, cfg, month_sales, reasons):
    """未取得のアカウントを1巡取得し、残った未取得アカウントを返す。"""
    done = {r["account"] for r in db.get_sales_payments(sales_ym)}
    for profile in profiles:
        code = normalize_account(profile.get("profile_name", ""))
        if code in done:
            continue
        log(f"取得中: {profile.get('profile_name')} ({code})")
        row, why = fetch_account(api, profile, sales_ym, cfg, month_sales)
        if row is None:
            reasons[code] = why
            log(f"  {code}: 未取得（{why}）")
            continue
        reasons.pop(code, None)
        db.upsert_sales_payment(sales_ym, code, row["pay_date"].isoformat(),
                                row["amount"], row["carry_over"], row["status"])
        done.add(code)
        log(f"  {code}: {sales_ym}分 振込 {row['amount']:,}円 を記録（{row['status']}）")
    accounts = [normalize_account(p.get("profile_name", "")) for p in profiles]
    return [a for a in accounts if a not in done]


def publish_web():
    """スマホ用の共有ページを更新する（失敗しても取得結果には影響させない）。"""
    try:
        proc = subprocess.run([sys.executable, str(BASE / "sales_publish.py")],
                              cwd=str(BASE), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log("共有ページ: " + ("更新しました" if proc.returncode == 0 else
                             f"更新に失敗（logs/sales_publish.log を確認）rc={proc.returncode}"))
    except Exception as e:
        log(f"共有ページ: 更新に失敗 - {e}")


def main(stack: contextlib.ExitStack) -> int:
    ap = argparse.ArgumentParser(description="FANZAの実際の振込額を取得する")
    ap.add_argument("--ym", help="売上月 YYYY-MM（既定: 前月）")
    ap.add_argument("--now", action="store_true",
                    help="振込日・16時の判定をせず、いますぐ取得する")
    ap.add_argument("--profile", action="append", help="対象プロファイル名（複数可）")
    ap.add_argument("--no-retry", action="store_true", help="未取得でも再試行しない")
    ap.add_argument("--dry-run", action="store_true", help="Discordに送らない")
    ap.add_argument("--force", action="store_true", help="通知済みでも再通知する")
    args = ap.parse_args()

    db.init_db()  # sales_payment テーブルが無い既存DBでも動くように
    cfg = sales_scraper.load_config()
    today = date.today()
    sales_ym = args.ym or payment.prev_ym(today.strftime("%Y-%m"))
    pay = payment.pay_date_for(sales_ym)
    catchup = int(cfg.get("payment_catchup_days", 7))

    profiles, raw = load_profiles(cfg, args.profile)
    accounts = [normalize_account(p.get("profile_name", "")) for p in profiles]
    done = {r["account"] for r in db.get_sales_payments(sales_ym)}
    pending = [a for a in accounts if a not in done]

    if not args.now:
        # 毎日起動されるので、対象外の日はログも残さず静かに終わる
        if today < pay or today > pay + timedelta(days=catchup):
            print(f"{sales_ym}分の振込日は {pay}。今日は対象外です。")
            return 0
        if not pending:
            print(f"{sales_ym}分の振込額は取得済みです。")
            return 0

    # 取得・リトライ待ちの間はPCをスリープさせない（sales_daily.py と同じ）
    stack.enter_context(keep_awake())
    log("=" * 60)
    log(f"振込額の取得 開始: {sales_ym}分（振込日 {pay}）")
    if not profiles:
        log("エラー: 対象プロファイルがありません")
        return 2
    log(f"対象アカウント: {len(profiles)}件 / 未取得 {len(pending)}件")

    check_at = hhmm_today(cfg.get("payment_check_time"), "16:00")
    if not args.now and today == pay and datetime.now() < check_at:
        log(f"振込額が載るのは{check_at:%H:%M}ごろのため、それまで待ちます")
        sleep_until(check_at.timestamp())

    interval = int(cfg.get("payment_retry_minutes", 30)) * 60
    limit = hhmm_today(cfg.get("payment_retry_until"), "23:30")
    month_sales = SalesReporter().month_amounts(sales_ym)
    api = AdsPowerAPI(raw.get("api_url", "http://127.0.0.1:50325"), raw.get("api_key", ""))

    reasons = {}
    while True:
        ok, msg = api.check_connection()
        if ok:
            pending = run_round(api, profiles, sales_ym, cfg, month_sales, reasons)
        else:
            log(f"AdsPowerに接続できません - {msg}")
            reasons = {a: "AdsPower未接続" for a in pending}
        if not pending or args.no_retry:
            break
        if datetime.now() + timedelta(seconds=interval) > limit:
            log(f"打ち切り時刻({limit:%H:%M})のためリトライ終了。未取得: {'、'.join(pending)}"
                "（翌日16:00の実行で続きを取得します）")
            break
        log(f"未取得 {len(pending)}件（{'、'.join(pending)}）… {interval // 60}分後に再確認")
        sleep_until(datetime.now().timestamp() + interval)

    summary = payment.load_summary(sales_ym, cfg)
    log(f"取得完了: 入金額合計 {summary.total:,}円 / 自分達の取り分合計 {summary.share_total:,}円"
        f" / 未取得 {len(pending)}件")
    # 確定前に出していた予想（この月を除いた実績の入金率）と比べる
    before = None
    try:
        from management.sales_stats import SalesStats
        months = SalesStats().months
        allsum, _, _ = payment.summaries_with_forecast(cfg, months)
        before = payment.forecast_before_confirm(sales_ym, cfg, months, allsum)
    except Exception as e:
        log(f"予想との比較を作れませんでした: {e}")
    text = payment.report_text(summary, month_sales, pending, forecast=before)

    if args.dry_run:
        log("--dry-run のため通知はスキップします。通知内容プレビュー:\n" + text)
        return 1 if pending else 0

    if summary.accounts:
        publish_web()

    reporter = SalesReporter()
    if not pending:
        if args.force or db.get_setting(NOTICE_GUARD_KEY, "") != sales_ym:
            if reporter.notify(text):
                db.set_setting(NOTICE_GUARD_KEY, sales_ym)
                log("Discord通知: 振込レポート送信")
            else:
                log("Discord通知: 送信失敗（webhook未設定の可能性）")
        return 0

    # 取れた分だけでも知らせる（未取得は明記）。同じ日には1回だけ
    if summary.accounts and (args.force or
                             db.get_setting(ALERT_GUARD_KEY, "") != today.isoformat()):
        if reporter.notify(text):
            db.set_setting(ALERT_GUARD_KEY, today.isoformat())
            log("Discord通知: 振込レポート（一部未取得）送信")
    return 1


if __name__ == "__main__":
    try:
        with contextlib.ExitStack() as stack:
            code = main(stack)
        sys.exit(code)
    except KeyboardInterrupt:
        log("中断されました")
        sys.exit(2)
    except Exception as e:
        log(f"致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
