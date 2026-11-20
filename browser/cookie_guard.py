"""
DMM/FANZA の Cookie 肥大化対策

ブラウザを使い続けると広告・計測系の Cookie が溜まり、リクエストヘッダが
サーバーの上限を超えて「400 Bad Request」になる。キャッシュ削除で直るが、
ログイン Cookie まで消えるため再ログインが必要になり、不審アクセス扱い
（パスワード変更要求）の原因になる。

実測（2026-11, FANZA-015〜023）では DMM の Cookie が合計約10KBあり、その半分は
同人サイト（Laravel）が発行する「40文字ランダム名・値が eyJ で始まる暗号化Cookie」
（1個700〜900バイト、フォームを開くたびに増える）だった。広告・計測系は750B程度。

そこでブラウザ接続直後に DMM ドメインの Cookie 量を確認し、上限に近ければ
このランダム名Cookieと広告・計測系だけを削除する（ログイン関連は名前指定で必ず残す）。
削除後もまだ大きい場合は、原因特定のため大きい Cookie の名前とサイズ
（値は記録しない）を logs/cookie_guard.log に残す。
"""
import re
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "cookie_guard.log"

# 対象ドメイン
TARGET_DOMAINS = ("dmm.co.jp", "dmm.com")

# この合計サイズ（バイト）を超えたら掃除する。
# 一般的なサーバーのヘッダ上限は 8KB 程度なので余裕をもって手前で掃除する。
CLEAN_THRESHOLD = 4000
# 掃除後もこれを超えていたらログに警告を残す
WARN_THRESHOLD = 6000

# ログイン・セッション関連（絶対に削除しない）
PROTECTED_NAMES = {
    "INT_SESID", "INT_SESID_SECURE", "secid", "login_secure_id", "login_session_id",
    "laravel_session", "XSRF-TOKEN", "ec_session", "althash", "has_althash",
    "check_done_login", "latestlogin", "connect.sid", "age_check_done", "ckcy",
    "deviceIdentify", "cdp_id", "cds_deliver_id",
}

# 広告・計測系 Cookie（削除してもログインには影響しない）
TRACKING_PATTERNS = [
    r"^_ga", r"^_gid$", r"^_gat", r"^_gcl", r"^__utm", r"^_dc_gtm",
    r"^FPID$", r"^FPLC$", r"^FPAU$", r"^FPGCL", r"^AMP_TOKEN$",
    r"^_fbp$", r"^_fbc$",
    r"^__gads$", r"^__gpi$", r"^__eoi$",
    r"^_uetsid$", r"^_uetvid$", r"^_clck$", r"^_clsk$",
    r"^_ttp$", r"^_tt_", r"^_pin_", r"^_pinterest",
    r"^_yjsu", r"^_yj", r"^_ts_yjad$", r"^_td", r"^_im_",
    r"^cto_", r"^_ldbr", r"^__lt__", r"^_ctx", r"^_rdt_uuid$",
    r"^krt\.", r"^_kr", r"^_sp_", r"^_hj", r"^mbox", r"^s_",
]
_TRACKING_RE = re.compile("|".join(TRACKING_PATTERNS))


_RANDOM_STATE_RE = re.compile(r"[A-Za-z0-9]{40}")


def _is_removable(c: dict) -> bool:
    """削除してよい Cookie か（ログイン関連は除外）"""
    name = c.get("name", "")
    if (name in PROTECTED_NAMES or name.startswith(("host-app_state", "__Host-", "__Secure-"))
            or name.startswith("login") or "SESID" in name):
        return False
    # 同人サイトの暗号化フォーム状態Cookie（40文字ランダム名・値は base64 JSON）
    if _RANDOM_STATE_RE.fullmatch(name) and c.get("value", "").startswith("eyJ"):
        return True
    return bool(_TRACKING_RE.search(name))


def _is_target(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(d == t or d.endswith("." + t) for t in TARGET_DOMAINS)


def _size(c: dict) -> int:
    # "name=value; " としてヘッダに乗るサイズの概算
    return len(c.get("name", "")) + len(c.get("value", "")) + 3


def _log(lines: list[str]):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for line in lines:
                f.write(f"[{ts}] {line}\n")
    except Exception:
        pass


def clean_dmm_cookies(driver, profile_id: str = "", threshold: int = CLEAN_THRESHOLD) -> str:
    """DMM の Cookie が多すぎればフォーム状態Cookieと広告・計測系だけ削除する。結果メッセージを返す。"""
    try:
        cookies = driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
    except Exception as e:
        return f"Cookie確認をスキップ（取得失敗: {e}）"

    dmm = [c for c in cookies if _is_target(c.get("domain"))]
    total = sum(_size(c) for c in dmm)
    if total <= threshold:
        return f"Cookie正常（DMM {len(dmm)}個 / {total}バイト）"

    removed, removed_bytes = 0, 0
    for c in dmm:
        if not _is_removable(c):
            continue
        try:
            driver.execute_cdp_cmd("Network.deleteCookies", {
                "name": c["name"], "domain": c["domain"], "path": c.get("path", "/"),
            })
            removed += 1
            removed_bytes += _size(c)
        except Exception:
            pass

    after = total - removed_bytes
    msg = (f"Cookie掃除: {profile_id} DMM {len(dmm)}個 {total}バイト → "
           f"不要Cookie {removed}個 削除 → {after}バイト")
    lines = [msg]
    if after > WARN_THRESHOLD:
        # まだ大きい: 原因特定用に大きい Cookie の名前とサイズを記録（値は残さない）
        rest = sorted((c for c in dmm if not _is_removable(c)),
                      key=_size, reverse=True)[:15]
        lines.append("  まだ大きい。残りの大きいCookie: " + ", ".join(
            f"{c['name']}@{c['domain']}={_size(c)}" for c in rest))
        msg += "（まだ大きいため logs/cookie_guard.log を確認してください）"
    _log(lines)
    print(msg)
    return msg
