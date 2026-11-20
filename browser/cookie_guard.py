"""
DMM/FANZA の Cookie 肥大化対策

ブラウザを使い続けると広告・計測系の Cookie が溜まり、リクエストヘッダが
サーバーの上限を超えて「400 Bad Request」になる。キャッシュ削除で直るが、
ログイン Cookie まで消えるため再ログインが必要になり、不審アクセス扱い
（パスワード変更要求）の原因になる。

そこでブラウザ接続直後に DMM ドメインの Cookie 量を確認し、上限に近ければ
広告・計測系の Cookie だけを削除する（ログイン関連は触らない）。
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
    """DMM の Cookie が多すぎれば広告・計測系だけ削除する。結果メッセージを返す。"""
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
        if not _TRACKING_RE.search(c.get("name", "")):
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
           f"広告・計測系 {removed}個 削除 → {after}バイト")
    lines = [msg]
    if after > threshold:
        # まだ大きい: 原因特定用に大きい Cookie の名前とサイズを記録（値は残さない）
        rest = sorted((c for c in dmm if not _TRACKING_RE.search(c.get("name", ""))),
                      key=_size, reverse=True)[:15]
        lines.append("  まだ大きい。残りの大きいCookie: " + ", ".join(
            f"{c['name']}@{c['domain']}={_size(c)}" for c in rest))
        msg += "（まだ大きいため logs/cookie_guard.log を確認してください）"
    _log(lines)
    print(msg)
    return msg
