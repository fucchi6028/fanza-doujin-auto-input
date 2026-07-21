"""
Discord webhook 通知（既存の日次レポート運用に合流できる汎用送信）

送信処理は本ツールに存在しなかったため新規実装。webhook URL は設定タブから
SQLite settings（キー: discord_webhook_url）に保存し、ここで読み出して送る。
URL 未設定なら黙ってスキップ（例外にしない）。

注意: Discord は User-Agent の無い/既定のリクエストを 403 で弾くことがあるため、
明示的に User-Agent を付ける。
"""
import json
import urllib.request
import urllib.error

USER_AGENT = "FanzaListingManager (https://localhost, 1.0)"


def send_detailed(webhook_url: str, content: str, username: str = "FANZA出品管理"):
    """送信し (成功bool, 詳細メッセージ) を返す。"""
    if not webhook_url or not webhook_url.strip():
        return False, "webhook URL が未設定です。"

    url = webhook_url.strip()
    if not url.startswith("http"):
        return False, f"URLの形式が不正です: {url[:40]}"
    if "discord.com/api/webhooks/" not in url and "discordapp.com/api/webhooks/" not in url:
        return False, ("Discord の webhook URL ではないようです。\n"
                       "『https://discord.com/api/webhooks/...』の形式か確認してください。")

    # Discord の content 上限 2000 文字
    content = content if len(content) <= 1900 else content[:1900] + "\n…(省略)"
    payload = json.dumps({"content": content, "username": username}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                return True, "送信成功"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        hint = ""
        if e.code == 401 or e.code == 404:
            hint = "\n→ URLが間違っている/webhookが削除された可能性があります。"
        elif e.code == 429:
            hint = "\n→ レート制限（送りすぎ）。少し待って再送してください。"
        elif e.code == 403:
            hint = "\n→ 権限エラー。webhookを作り直してURLを貼り直してください。"
        return False, f"HTTP {e.code}: {e.reason}{hint}\n{body}"
    except urllib.error.URLError as e:
        return False, (f"ネットワークに接続できません: {e.reason}\n"
                       "→ VPN/プロキシ/ファイアウォール、またはネット接続を確認してください。")
    except Exception as e:
        return False, f"送信エラー: {e}"


def send(webhook_url: str, content: str, username: str = "FANZA出品管理") -> bool:
    """従来インターフェース: 成功で True。詳細が要る場合は send_detailed を使う。"""
    ok, detail = send_detailed(webhook_url, content, username)
    if not ok:
        print(f"[discord] {detail}")
    return ok
