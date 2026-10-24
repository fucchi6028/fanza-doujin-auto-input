"""売上ボード（スマホ用Webページ）の公開

DBの集計結果を合言葉で暗号化し、GitHub Pages 用のリポジトリへ push する。
公開先に置くのは web/index.html と暗号化済みの data.json だけなので、
URLを知られても合言葉が無ければ中身は読めない。

  python sales_publish.py              # 集計 → 暗号化 → commit → push
  python sales_publish.py --no-push    # 手元に書き出すだけ（動作確認用）
  python sales_publish.py --info       # 公開URLと合言葉を表示

設定は config/sales_web.json（gitignore済み）。初回実行時に雛形を作り、
合言葉が空なら自動生成する。
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from browser.sales_scraper import load_config as load_sales_config
from management import payment
from management.sales_stats import SalesStats

CONFIG_PATH = BASE / "config" / "sales_web.json"
SOURCE_HTML = BASE / "web" / "index.html"
LOG_PATH = BASE / "logs" / "sales_publish.log"

DEFAULT_CONFIG = {
    "remote": "",                       # 例: https://github.com/<ユーザー名>/sales-board.git
    "site_url": "",                     # 例: https://<ユーザー名>.github.io/sales-board/
    "repo_dir": ".publish/sales-board",  # 公開リポジトリの作業用クローン（gitignore済み）
    "branch": "main",
    "passphrase": "",                   # 空なら初回に自動生成
    "iterations": 210000,
}

# 見間違えやすい文字（0/O, 1/l/I）を抜いた合言葉用の文字種
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "cp932"
        print(line.encode(enc, "replace").decode(enc), flush=True)
    try:
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 設定 ──────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if not cfg.get("passphrase"):
        cfg["passphrase"] = "-".join(
            "".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))
        log("合言葉を新しく作りました（config/sales_web.json に保存します）")
    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 暗号化（ブラウザの WebCrypto で復号できる形） ──────────
def encrypt(plaintext: bytes, passphrase: str, iterations: int) -> dict:
    """PBKDF2-SHA256 で鍵を作り AES-GCM で暗号化する。

    AES-GCM の出力は「暗号文＋認証タグ」の連結で、WebCrypto の
    crypto.subtle.decrypt がそのまま受け取れる形式。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, 32)
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")
    return {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "cipher": "AES-GCM",
        "iterations": iterations,
        "salt": b64(salt),
        "iv": b64(iv),
        "ciphertext": b64(ciphertext),
    }


PAGE_REV_MARK = "__PAGE_REV__"


def page_rev() -> str:
    """web/index.html の版（中身のハッシュ）。

    GitHub Pages は index.html を10分キャッシュさせるうえ、スマホのホーム画面
    アプリはさらに長く古いページを持ち続ける。data.json は毎回取り直されるので、
    そこに版を入れておき、ページ側で自分の版と違えば読み込み直させる。
    """
    return hashlib.sha256(SOURCE_HTML.read_bytes()).hexdigest()[:12]


def build_payload(stats: SalesStats) -> dict:
    """ページが表示に使うデータ一式（暗号化される中身）。"""
    return {
        "page_rev": page_rev(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "latest_date": stats.latest_date(),
        "accounts": stats.accounts,
        "labels": {code: stats.labels.get(code, "") for code in stats.accounts},
        "daily": stats.daily,
        "months": stats.months,
        "payments": build_payments(stats),
    }


def month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def build_payments(stats: SalesStats) -> dict:
    """売上月ごとの振込。確定済みは実額、まだの月は実績の入金率からの予想。

    2割引き等の計算は management.payment と同じ値を渡す。kind は
    "confirmed"（振込確認済み）／"forecast"（予想）／"none"（予想も出せない）。
    complete は「月末までの売上がそろっているか」（False なら途中経過の予想）。
    """
    cfg = load_sales_config()
    rate, exclude = payment.share_settings(cfg)
    summaries, pay_rate, basis = payment.summaries_with_forecast(cfg, stats.months)
    latest = stats.latest_date()
    out = {}
    for ym in sorted(set(stats.month_list) | set(summaries)):
        summ = summaries.get(ym)
        days = stats.days_of_month(ym)
        entry = {"due": payment.pay_date_for(ym).isoformat(), "rows": {},
                 "kind": "none", "complete": bool(latest) and latest >= month_end(ym),
                 "as_of": days[-1] if days else ""}
        if summ:
            entry.update({
                "kind": "forecast" if summ.is_forecast else "confirmed",
                "pay_date": summ.pay_date,
                "total": summ.total,
                "share_total": summ.share_total,
                "per_person": summ.per_person,
                "diff_total": summ.diff_total,
                "carry_total": summ.carry_total,
                "deducted_person": summ.deducted_person,
                "groups": [dict(name=name, accounts=codes, **summ.group_totals(codes))
                           for name, codes in summ.groups],
            })
            if not summ.is_forecast:
                before = payment.forecast_before_confirm(ym, cfg, stats.months, summaries)
                entry["forecast_total"] = before.total if before else None
            for code in summ.accounts:
                r = summ.rows[code]
                entry["rows"][code] = {
                    "amount": summ.amount(code),
                    "share": summ.share(code),
                    "diff": summ.diff(code),
                    "carry_over": int(r.get("carry_over") or 0),
                    "status": r.get("status") or "",
                }
        out[ym] = entry
    deduct = payment.person_deduct_settings(cfg)
    return {"rate": rate, "exclude": sorted(exclude), "months": out,
            "pay_rate": pay_rate, "pay_rate_basis": basis,
            "person_deduct": ({"name": deduct[0], "account": deduct[1]} if deduct else None)}


# ── git ───────────────────────────────────────────────
def git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def prepare_repo(repo_dir: Path, remote: str, branch: str):
    """公開用リポジトリの作業ディレクトリを用意する（無ければ作る）。"""
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        git(["init"], repo_dir)
        git(["checkout", "-B", branch], repo_dir)
        log(f"公開用リポジトリを初期化しました: {repo_dir}")
    # commit するには名前とメールが要る。グローバル設定が無い環境もあるので、
    # このプロジェクトのリポジトリに設定されている値を引き継ぐ
    for key in ("user.name", "user.email"):
        if git(["config", "--get", key], repo_dir, check=False).stdout.strip():
            continue
        value = git(["config", "--get", key], BASE, check=False).stdout.strip()
        if value:
            git(["config", key, value], repo_dir)

    if remote:
        current = git(["remote"], repo_dir).stdout.split()
        if "origin" in current:
            git(["remote", "set-url", "origin", remote], repo_dir)
        else:
            git(["remote", "add", "origin", remote], repo_dir)


def write_site(repo_dir: Path, blob: dict):
    html = SOURCE_HTML.read_text(encoding="utf-8").replace(PAGE_REV_MARK, page_rev())
    (repo_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    (repo_dir / "data.json").write_text(
        json.dumps(blob, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # GitHub Pages の Jekyll 処理を通さない（素のファイルをそのまま配信する）
    (repo_dir / ".nojekyll").write_text("", encoding="utf-8")
    (repo_dir / "README.md").write_text(
        "# sales board\n\n"
        "静的ページと暗号化済みデータだけを置く公開用リポジトリです。\n"
        "data.json は合言葉（PBKDF2-SHA256 + AES-GCM）で暗号化されています。\n",
        encoding="utf-8")


def commit_and_push(repo_dir: Path, branch: str, push: bool) -> bool:
    git(["add", "-A"], repo_dir)
    if not git(["status", "--porcelain"], repo_dir).stdout.strip():
        log("変更なし（前回と同じ内容）")
        return True
    message = f"売上データ更新 {datetime.now():%Y-%m-%d %H:%M}"
    result = git(["commit", "-m", message], repo_dir, check=False)
    if result.returncode:
        log(f"commit に失敗しました: {result.stderr.strip() or result.stdout.strip()}")
        return False
    log(f"commit しました: {message}")
    if not push:
        log("--no-push のため push はしません")
        return True
    result = git(["push", "-u", "origin", branch], repo_dir, check=False)
    if result.returncode:
        log("push に失敗しました:\n" + (result.stderr.strip() or result.stdout.strip()))
        log("→ GitHubに空のリポジトリを作ってあるか、remote のURLが正しいか確認してください")
        return False
    log("push しました")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="売上ボード（スマホ用）の公開")
    ap.add_argument("--no-push", action="store_true", help="手元に書き出すだけでpushしない")
    ap.add_argument("--info", action="store_true", help="公開URLと合言葉を表示して終了")
    args = ap.parse_args()

    cfg = load_config()
    if args.info:
        print(f"公開URL : {cfg['site_url'] or '(未設定)'}")
        print(f"合言葉  : {cfg['passphrase']}")
        print(f"remote  : {cfg['remote'] or '(未設定)'}")
        return 0

    log("=" * 60)
    stats = SalesStats()
    if not stats.month_list:
        log("売上データがありません。公開を中止します")
        return 2

    payload = build_payload(stats)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = encrypt(raw, cfg["passphrase"], int(cfg["iterations"]))
    log(f"集計: {len(stats.accounts)}アカウント / {len(stats.date_list)}日分 / "
        f"累計 {stats.grand_total():,}円（元データ {len(raw):,}バイト）")

    repo_dir = Path(cfg["repo_dir"])
    if not repo_dir.is_absolute():
        repo_dir = BASE / repo_dir
    prepare_repo(repo_dir, cfg["remote"], cfg["branch"])
    write_site(repo_dir, blob)

    if not cfg["remote"]:
        log("remote が未設定のため書き出しのみ行いました: " + str(repo_dir))
        return 1
    ok = commit_and_push(repo_dir, cfg["branch"], push=not args.no_push)
    if ok and cfg["site_url"]:
        log(f"公開URL: {cfg['site_url']}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
