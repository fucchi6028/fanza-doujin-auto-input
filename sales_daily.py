"""
売上（卸金額）の自動集計 → Discord通知（Windowsタスクスケジューラから毎朝実行）

処理の流れ:
  1. 対象日（既定=前日）の卸金額を、AdsPowerの各プロファイルで順に取得
     ・単日   : /sales/all/catalog/all/0/{対象日}/{対象日}
     ・月累計 : /sales/all/catalog/all/0/{月初}/{対象日}
  2. FANZA側がまだ更新していないアカウントは時間をおいてリトライ
     （ページの「(YYYY/MM/DD HH:MM更新)」で未更新か0円かを判定する）
  3. 全アカウント取得できたら Discord に通知（前日合計＋当月累計＋アカウント別内訳）
     ・打ち切り時刻までに揃わなかった場合は速報を送らず、未取得アラートのみ送る
       （全件そろった次の実行であらためて速報を送る）
  4. 月初なら前月確定の月次合計も通知（月末分は翌日＝月初に確定するため）

使い方:
  python sales_daily.py                     # 前日分を取得して通知
  python sales_daily.py --date 2026-08-24   # 対象日を指定
  python sales_daily.py --profile FANZA-001 --dry-run --no-retry   # 動作確認
  python sales_daily.py --force             # 通知済みでも再通知する

終了コード: 0=全件取得して通知 / 1=一部未取得 / 2=エラー
"""
import argparse
import contextlib
import ctypes
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from browser import sales_scraper
from browser.adspower import AdsPowerAPI, AdsPowerBrowser
from management.db import normalize_account
from management.sales import SalesReporter, yesterday

LOG_PATH = BASE / "logs" / "sales_daily.log"


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # コンソールがcp932だと絵文字入りの通知文で落ちるため、表示だけ置換する
        enc = sys.stdout.encoding or "cp932"
        print(line.encode(enc, "replace").decode(enc), flush=True)
    try:
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # ログ書き込み失敗で本処理は止めない


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_awake():
    """処理中（リトライ待ちを含む）はPCをスリープさせない。

    タスクスケジューラのWakeToRunで起きても、リトライの待機中は無操作のため
    アイドルスリープが働き、プロセスごと凍結して通知が届かなくなる。
    SetThreadExecutionState でアイドルタイマーを止め、終了時に解除する。
    """
    enabled = False
    try:
        enabled = bool(ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    except Exception as e:
        log(f"スリープ抑止を設定できませんでした（続行します）: {e}")
    if enabled:
        log("スリープ抑止: 有効（処理終了まで自動スリープしません）")
    try:
        yield
    finally:
        if enabled:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                log("スリープ抑止: 解除")
            except Exception:
                pass


def sleep_until(deadline_ts: float) -> float:
    """指定時刻まで待つ。実際に経過した秒数を返す。

    time.sleep はスリープ中の時間も消費するため、細かく刻んで壁時計で待つ。
    """
    start = time.time()
    while True:
        remain = deadline_ts - time.time()
        if remain <= 0:
            break
        time.sleep(min(remain, 30))
    return time.time() - start


def load_profiles(cfg: dict, only=None):
    """profiles_config.json のプロファイル一覧（設定・引数で絞り込み可）。"""
    path = BASE / "config" / "profiles_config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    profiles = data.get("profiles", [])
    wanted = only or cfg.get("target_profiles") or []
    if wanted:
        profiles = [p for p in profiles
                    if p.get("profile_name") in wanted or p.get("profile_id") in wanted]
    return profiles, data


def deadline_today(cfg: dict) -> datetime:
    """リトライを打ち切る時刻（既定 12:00）。

    スリープ復帰などで起動が遅れ、実行時点で既に打ち切り時刻を過ぎている場合は
    そこから retry_grace_minutes（既定60分）だけ猶予を取る。1巡で打ち切ると、
    起動直後だけプロキシ接続に失敗するプロファイルを取りこぼすため。
    """
    hhmm = str(cfg.get("retry_until", "12:00"))
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except Exception:
        h, m = 12, 0
    now = datetime.now()
    limit = now.replace(hour=h, minute=m, second=0, microsecond=0)
    grace = int(cfg.get("retry_grace_minutes", 60))
    if limit <= now:
        limit = now + timedelta(minutes=grace)
        log(f"打ち切り時刻({hhmm})を過ぎているため、{grace}分の猶予でリトライします")
    return limit


# 起動直後はプロキシ接続が安定せず、これらのエラーで初回だけ失敗することがある
TRANSIENT_ERROR_MARKS = (
    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED",
    "ERR_CONNECTION_TIMED_OUT", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_EMPTY_RESPONSE",
    "ブラウザ起動失敗",
)


def is_transient(msg: str) -> bool:
    """時間をおけば直る類のエラーか（＝すぐ再試行する価値があるか）。"""
    return any(mark in (msg or "") for mark in TRANSIENT_ERROR_MARKS)


def probe_proxy(api: AdsPowerAPI, pid: str, target: str = "dojin.dmm.co.jp:443") -> str:
    """プロファイルのプロキシへ AdsPower を通さず直接 CONNECT し、応答を返す。

    ブラウザが ERR_TUNNEL_CONNECTION_FAILED のとき、プロキシ業者側の不調
    （502 やタイムアウトが返る）か、AdsPower/ブラウザ側の問題（直接なら 200）かを
    ログで切り分けるための診断。失敗しても本処理には影響させない。
    """
    import base64
    import socket
    try:
        prof = next((p for p in api.get_profiles() if p.get("user_id") == pid), None)
        px = (prof or {}).get("user_proxy_config") or {}
        host, port = px.get("proxy_host"), px.get("proxy_port")
        if not host or not port:
            return "プロキシ設定なし"
        start = time.time()
        with socket.create_connection((host, int(port)), timeout=30) as so:
            so.settimeout(60)
            req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
            if px.get("proxy_user"):
                cred = f"{px['proxy_user']}:{px.get('proxy_password', '')}".encode()
                req += f"Proxy-Authorization: Basic {base64.b64encode(cred).decode()}\r\n"
            so.sendall((req + "\r\n").encode())
            status = so.recv(4096).decode(errors="replace").split("\r\n")[0]
        return f"{host}:{port} → {status or '(応答なし)'}（{time.time() - start:.1f}秒）"
    except Exception as e:
        return f"直接接続も失敗: {type(e).__name__} {e}"


def fetch_one(api: AdsPowerAPI, profile: dict, target: date, cfg: dict, job=None):
    """1プロファイル分を取得する。一時的な通信エラーは短い間隔で即リトライする。

    002 のようにプロキシ経由のプロファイルは、ブラウザ起動直後の1回目だけ
    ERR_TUNNEL_CONNECTION_FAILED で落ちることがある。10分待たずにその場で
    数十秒おいて張り直すだけで通るので、ここで吸収する。
    """
    name = profile.get("profile_name", profile["profile_id"])
    attempts = int(cfg.get("profile_retry", 2)) + 1
    wait = int(cfg.get("profile_retry_wait_sec", 30))
    res = {"error": "未実行"}
    for attempt in range(attempts):
        res = fetch_once(api, profile, target, cfg, job)
        err = res.get("error", "")
        if "TUNNEL" in err or "PROXY" in err:
            log(f"  {name}: プロキシ直接診断 {probe_proxy(api, profile['profile_id'])}")
        if not err or not is_transient(err) or attempt == attempts - 1:
            return res
        log(f"  {name}: 一時的なエラーのため{wait}秒後に再試行します"
            f"（{attempt + 1}/{attempts - 1}）")
        time.sleep(wait)
    return res


def fetch_once(api: AdsPowerAPI, profile: dict, target: date, cfg: dict, job=None):
    """1プロファイルを1回だけ取得する。戻り値: fetch_daily_and_month の dict / None（失敗）。

    job(driver) を渡すと売上ページの代わりにそれを実行し、戻り値をそのまま返す
    （振込額の取得 sales_payment.py が使う）。

    こちらで起動したブラウザだけを閉じる（作業中のブラウザは閉じない）。
    """
    pid = profile["profile_id"]
    name = profile.get("profile_name", pid)

    already_active = False
    try:
        ok, data = api.check_browser_status(pid)
        already_active = ok and str(data.get("status", "")).lower() == "active"
    except Exception:
        pass

    browser = AdsPowerBrowser(api, headless=bool(cfg.get("headless", True)))
    try:
        if not browser.start(pid):
            log(f"  {name}: ブラウザ起動失敗 - {browser.last_error}")
            return {"error": "ブラウザ起動失敗"}
        if job is not None:
            return job(browser.get_driver())
        return sales_scraper.fetch_daily_and_month(
            browser.get_driver(), target, cfg, report=log)
    except sales_scraper.SalesLoginRequired as e:
        log(f"  {name}: {e}")
        return {"error": "ログイン切れ（AdsPowerで再ログインしてください）"}
    except sales_scraper.SalesFetchError as e:
        log(f"  {name}: ページ解析に失敗 - {e}")
        if browser.driver:
            sales_scraper.dump_page(browser.driver,
                                    BASE / "logs" / f"sales_miss_{name}.html")
        return {"error": "ページ解析に失敗"}
    except Exception as e:
        log(f"  {name}: 取得エラー - {e}")
        return {"error": f"取得エラー: {e}"}
    finally:
        if already_active:
            # 元から開いていたブラウザは閉じず、Selenium接続だけ切る
            if browser.driver:
                try:
                    browser.driver.quit()
                except Exception:
                    pass
        else:
            browser.stop()


def publish_board():
    """スマホ用の共有ページを今のDBの内容で更新する（失敗しても集計は続ける）。

    バッチは sales_daily.py の終了後にも sales_publish.py を実行するが、
    リトライが長引くとそれまで共有ページが前日のままになるため途中でも呼ぶ。
    """
    import subprocess
    try:
        proc = subprocess.run([sys.executable, str(BASE / "sales_publish.py")],
                              cwd=str(BASE), capture_output=True, timeout=600)
        log(f"共有ページ更新: {'成功' if proc.returncode == 0 else f'失敗 rc={proc.returncode}'}"
            "（詳細は logs/sales_publish.log）")
    except Exception as e:
        log(f"共有ページ更新に失敗: {e}")


def run_round(api, profiles, reporter, target, cfg, reasons=None):
    """未取得のプロファイルを1巡して取得。残った未取得アカウントを返す。

    reasons: {アカウントコード: 未取得の理由} を埋める（Discord通知に載せる）。
    """
    reasons = reasons if reasons is not None else {}
    accounts = [normalize_account(p.get("profile_name", "")) for p in profiles]
    pending = reporter.missing_accounts(target.isoformat(), accounts)
    if not pending:
        return []

    for profile in profiles:
        code = normalize_account(profile.get("profile_name", ""))
        if code not in pending:
            continue
        log(f"取得中: {profile.get('profile_name')} ({code})")
        res = fetch_one(api, profile, target, cfg)
        if res.get("error"):
            reasons[code] = res["error"]
            log(f"  {code}: 取得失敗（{res['error']}）")
        elif not res["fresh"]:
            reasons[code] = "FANZA側がまだ未更新"
            log(f"  {code}: FANZA側が未更新のためスキップ（次回リトライ）")
        else:
            reasons.pop(code, None)
            reporter.record(target.isoformat(), code, res["day"])
            reporter.record_month(res["ym"], code, res["month"], target)
            log(f"  {code}: 前日 {res['day']:,}円 / {res['ym']}累計 {res['month']:,}円 を記録")

    return reporter.missing_accounts(target.isoformat(), accounts)


def main() -> int:
    ap = argparse.ArgumentParser(description="FANZA売上（卸金額）の自動集計・通知")
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（既定: 前日）")
    ap.add_argument("--profile", action="append",
                    help="対象プロファイル名（複数指定可。動作確認用）")
    ap.add_argument("--no-retry", action="store_true", help="未取得でもリトライしない")
    ap.add_argument("--dry-run", action="store_true", help="Discordに送らない")
    ap.add_argument("--force", action="store_true", help="通知済みでも再通知する")
    args = ap.parse_args()

    target = date.fromisoformat(args.date) if args.date else yesterday()
    cfg = sales_scraper.load_config()

    log("=" * 60)
    log(f"売上集計 開始: 対象日 {target}")

    profiles, raw = load_profiles(cfg, args.profile)
    if not profiles:
        log("エラー: 対象プロファイルがありません")
        return 2
    log(f"対象アカウント: {len(profiles)}件 "
        f"({'、'.join(p.get('profile_name', '?') for p in profiles)})")

    api = AdsPowerAPI(raw.get("api_url", "http://127.0.0.1:50325"), raw.get("api_key", ""))
    ok, msg = api.check_connection()
    if not ok:
        log(f"エラー: AdsPowerに接続できません - {msg}")
        return 2

    reporter = SalesReporter()
    accounts = [normalize_account(p.get("profile_name", "")) for p in profiles]
    interval = int(cfg.get("retry_interval_minutes", 10)) * 60
    limit = deadline_today(cfg)

    reasons = {}
    pending = run_round(api, profiles, reporter, target, cfg, reasons)
    if pending and len(pending) < len(accounts) and not args.dry_run and not args.no_retry:
        # 一部だけ欠けたときは、リトライで昼過ぎまで待たせず取れた分を先に届ける
        # （全件 FANZA 未更新のような「まだ早い」だけのときは送らない）
        log(f"未取得 {len(pending)}件（{'、'.join(pending)}）: 取得済み分を先に通知・公開します")
        if reporter.notify_incomplete(target.isoformat(), accounts,
                                      reasons=reasons, force=args.force):
            log("Discord通知: 途中経過（未取得アラート）送信")
        publish_board()
    while pending and not args.no_retry:
        if datetime.now() >= limit:
            log(f"打ち切り時刻({limit:%H:%M})を過ぎたためリトライ終了。未取得: {'、'.join(pending)}")
            break
        log(f"未取得 {len(pending)}件（{'、'.join(pending)}）… {interval // 60}分後に再試行")
        slept = sleep_until(min(datetime.now().timestamp() + interval, limit.timestamp()))
        if slept > interval * 2:
            # スリープ等で待機が大幅に延びた場合は打ち切り時刻を再判定させる
            log(f"待機が {slept // 60:.0f}分に延びました（PCがスリープした可能性）")
        pending = run_round(api, profiles, reporter, target, cfg, reasons)

    total = reporter.day_total(target.isoformat())
    log(f"取得完了: 前日合計 {total:,}円 / 未取得 {len(pending)}件")

    if args.dry_run:
        log("--dry-run のため通知はスキップします。通知内容プレビュー:")
        if pending:
            log("\n" + reporter.incomplete_report_text(
                target.isoformat(), accounts, reasons))
        else:
            log("\n" + reporter.daily_report_text(target.isoformat(), accounts, reasons))
        return 1 if pending else 0

    # 売上速報は「全アカウント取得できたとき」だけ送る。
    # 途中経過で送ると実際より小さい合計が通知され、次の実行では通知済みガードに
    # 引っかかって正しい数字が届かなくなるため。
    if pending:
        log(f"未取得 {len(pending)}件（{'、'.join(pending)}）のため売上速報は送りません"
            "（全件そろった次回の実行で通知します）")
        if reporter.notify_incomplete(target.isoformat(), accounts,
                                      reasons=reasons, force=args.force):
            log("Discord通知: 未取得アラート送信")
        return 1

    if reporter.notify_daily(target.isoformat(), accounts, force=args.force,
                             reasons=reasons):
        log("Discord通知: 日次レポート送信")
    else:
        log("Discord通知: 送信スキップ or 失敗（通知済み/webhook未設定の可能性）")

    # 月初なら前月確定を通知（月末分は当日=月初に取得されるのでここで確定する）
    if reporter.maybe_notify_monthly(accounts=accounts, force=args.force):
        log("Discord通知: 月次確定レポート送信")

    return 1 if pending else 0


if __name__ == "__main__":
    try:
        with keep_awake():
            sys.exit(main())
    except KeyboardInterrupt:
        log("中断されました")
        sys.exit(2)
    except Exception as e:
        log(f"致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
