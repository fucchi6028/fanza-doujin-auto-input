"""
出品管理表 / 予約出品管理表 の高レベルAPI（ListingManager）

3操作をトリガーに自動チェックを入れ、ログ・ステータス・繰越・重複検知・
Discord通知・レポート抽出をまとめて提供する。GUI(main_tk.py)はこのクラス
だけを呼べばよい。
"""
import re
from datetime import date, datetime

from management import db
from management import discord_notify
from management.db import (
    TABLE_LISTING, TABLE_RESERVED, SLOTS_PER_MONTH,
    STATUS_REJECTED, STATUS_LISTED, STATUS_RESERVED,
    OP_LISTING, OP_RESERVED, OP_TRAILER,
    OP_MANUAL, OP_STATUS, OP_ROLLOVER,
    normalize_account, account_sort_key, is_occupied,
)

WEBHOOK_KEY = "discord_webhook_url"
MONTHLY_NOTICE_KEY = "last_monthly_notice_ym"

TABLE_LABELS = {TABLE_LISTING: "出品管理表", TABLE_RESERVED: "予約出品管理表"}

# ── 4段階ステータス（GUI・Excel共用） ───────────────────
# 未着手 → 進行中 → （予約出品済） → 完了
# 出品管理表(通常): 作品入力した時点で「完了」。予約出品済の段階は無い。
# 予約出品管理表   : 予約作品入力＝「予約出品済」。その後に配信申請したら「完了」。
STATE_NONE = "未着手"
STATE_WIP = "進行中"
STATE_RESERVED = "予約出品済"
STATE_DONE = "完了"

# 色キー（GUI=Treeviewタグ名 / Excel=塗り分けキーとして共用）
STATE_COLOR = {
    STATE_NONE: "gray",
    STATE_WIP: "yellow",
    STATE_RESERVED: "blue",
    STATE_DONE: "green",
}


# ── 年月ユーティリティ ──────────────────────────────────
def ym_of(d: date) -> str:
    return d.strftime("%Y-%m")


def current_ym() -> str:
    return ym_of(date.today())


def add_months(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    idx = (y * 12 + (m - 1)) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def next_ym(ym=None) -> str:
    return add_months(ym or current_ym(), 1)


def prev_ym(ym=None) -> str:
    return add_months(ym or current_ym(), -1)


def normalize_title(title: str) -> str:
    """重複判定用のタイトル正規化（空白除去・全角/丸数字などの軽い吸収）。"""
    if not title:
        return ""
    t = title.strip()
    # 波ダッシュ/全角チルダを統一
    t = t.replace("〜", "~").replace("～", "~")
    # 丸数字 ①..⑳ -> 1..20
    for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", start=1):
        t = t.replace(ch, str(i))
    # 全角英数を半角へ
    t = t.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))
    t = re.sub(r"\s+", "", t)  # 空白全除去
    return t.lower()


class ListingManager:
    def __init__(self, db_path=None, notifier=None):
        self.db_path = db_path
        # notifier(text)->bool を差し替え可能に（テスト用）。既定はDiscord。
        self._notifier = notifier
        db.init_db(self.db_path)

    # ── 通知 ──────────────────────────────────────────
    def get_webhook(self) -> str:
        return db.get_setting(WEBHOOK_KEY, "", self.db_path) or ""

    def set_webhook(self, url: str):
        db.set_setting(WEBHOOK_KEY, url or "", self.db_path)

    def notify(self, text: str) -> bool:
        if self._notifier is not None:
            try:
                return bool(self._notifier(text))
            except Exception as e:
                print(f"[manager] notifier failed: {e}")
                return False
        return discord_notify.send(self.get_webhook(), text)

    def notify_detailed(self, text: str):
        """(成功bool, 詳細メッセージ) を返す。テスト送信・診断用。"""
        if self._notifier is not None:
            try:
                return bool(self._notifier(text)), "カスタム通知"
            except Exception as e:
                return False, str(e)
        return discord_notify.send_detailed(self.get_webhook(), text)

    # ── アカウント同期 / 月次準備 ─────────────────────
    def sync_accounts(self, profile_names):
        """プロファイル名リストから管理アカウントを登録（正規化して upsert）。"""
        for name in profile_names or []:
            code = normalize_account(name)
            if code:
                db.upsert_account(code, 1, self.db_path)

    def accounts(self):
        return db.list_accounts(self.db_path)

    def account_names(self):
        """{アカウントコード: サークル名} を返す。"""
        return db.account_names(self.db_path)

    def import_excel(self, path, months=None, overwrite=True):
        """既存の出品管理表Excelを取り込む。サマリdictを返す。"""
        from management import excel_import
        return excel_import.import_workbook(self, path, months=months, overwrite=overwrite)

    def ensure_months(self, months=None):
        """当月・翌月（既定）のレコードを用意。翌月を新規作成したら繰越ログを残す。"""
        accts = self.accounts()
        cur = current_ym()
        nxt = next_ym(cur)
        months = months or [cur, nxt]
        newly = []
        for ym in months:
            if db.ensure_month(ym, accts, self.db_path):
                newly.append(ym)
        if nxt in newly:
            db.add_log(nxt, None, None, None, OP_ROLLOVER,
                       detail=f"{nxt} のレコードを自動作成（繰越）", db_path=self.db_path)
        return newly

    # ── 枠検索 / 占有数 ───────────────────────────────
    def _empty_slot_no(self, ym, account, table_type):
        for row in db.get_slots(ym, account, table_type, self.db_path):
            if not is_occupied(row):
                return row["slot_no"]
        return None

    def occupied_count(self, ym, account, table_type):
        return sum(1 for r in db.get_slots(ym, account, table_type, self.db_path)
                   if is_occupied(r))

    # ── 重複検知 ──────────────────────────────────────
    def find_duplicates(self, title, months=None, table_type=None, exclude_account=None):
        """同一タイトルが（当月・翌月など）既に登録済みの枠を探す。"""
        if not title:
            return []
        key = normalize_title(title)
        months = months or [current_ym(), next_ym()]
        types = [table_type] if table_type else [TABLE_LISTING, TABLE_RESERVED]
        hits = []
        for ym in months:
            for tt in types:
                for r in db.get_month_rows(ym, tt, self.db_path):
                    if not is_occupied(r) or not r.get("product_title"):
                        continue
                    if exclude_account and r["account"] == exclude_account:
                        continue
                    if normalize_title(r["product_title"]) == key:
                        hits.append({
                            "account": r["account"], "ym": r["ym"],
                            "table_type": tt, "slot_no": r["slot_no"],
                            "product_title": r["product_title"],
                        })
        return hits

    def check_duplicate_alert(self, title, target_table):
        """登録直前チェック。重複があれば通知して結果を返す（自動処理を止める判断はGUI）。"""
        hits = self.find_duplicates(title)
        if hits:
            where = "、".join(
                f"{h['account']}({TABLE_LABELS[h['table_type']]}/{h['ym']})" for h in hits
            )
            self.notify(f"⚠️ 重複登録の疑い\n作品「{title}」は既に登録済み: {where}")
        return hits

    # ── 記録（3操作のトリガー） ───────────────────────
    def _record(self, ym, account, table_type, title, product_id, op):
        self.ensure_months([ym])
        slot_no = self._empty_slot_no(ym, account, table_type)
        if slot_no is None:
            return {"ok": False, "reason": "full", "ym": ym, "account": account,
                    "table_type": table_type}
        db.update_slot(ym, account, table_type, slot_no,
                       checked=True, product_title=title, product_id=product_id,
                       source_op=op, db_path=self.db_path)
        db.add_log(ym, account, table_type, slot_no, op,
                   detail=f"{TABLE_LABELS[table_type]} 枠{slot_no} にチェック",
                   product_title=title, db_path=self.db_path)
        filled = self.occupied_count(ym, account, table_type)
        result = {"ok": True, "ym": ym, "account": account, "table_type": table_type,
                  "slot_no": slot_no, "filled": filled, "notified": []}
        if filled >= SLOTS_PER_MONTH:
            msg = (f"✅ 打ち止め: {account} が {ym} の{TABLE_LABELS[table_type]}で "
                   f"3/3 到達（当月これ以上の出品なし）")
            if self.notify(msg):
                result["notified"].append(msg)
        return result

    def record_listing(self, account, title, product_id=None, ym=None):
        """作品入力（通常出品）→ 出品管理表の空き枠にチェック。"""
        return self._record(ym or current_ym(), account, TABLE_LISTING,
                            title, product_id, OP_LISTING)

    def record_reserved(self, account, title, product_id=None, ym=None):
        """予約作品入力（予約出品）→ 予約出品管理表の空き枠にチェック。"""
        return self._record(ym or current_ym(), account, TABLE_RESERVED,
                            title, product_id, OP_RESERVED)

    def mark_reserved_listed(self, account, title):
        """配信申請した予告作品に対応する『予約出品管理表』の枠を『出品済』(完了)にする。

        予約作品はタイトルで登録されているので、月をまたいで account の予約枠を
        タイトル突合し、該当枠の status を STATUS_LISTED に更新する。
        戻り値: 更新した枠情報 dict / 既に出品済なら already=True / 見つからなければ None。
        """
        if not account or not title:
            return None
        key = normalize_title(title)
        for r in db.get_account_slots(account, TABLE_RESERVED, self.db_path):
            if not is_occupied(r) or not r.get("product_title"):
                continue
            if normalize_title(r["product_title"]) != key:
                continue
            if r.get("status") == STATUS_LISTED:
                return {"ym": r["ym"], "slot_no": r["slot_no"], "already": True,
                        "product_title": r.get("product_title")}
            old = r.get("status") or ""
            db.update_slot(r["ym"], account, TABLE_RESERVED, r["slot_no"],
                           status=STATUS_LISTED, source_op=OP_STATUS,
                           db_path=self.db_path)
            db.add_log(r["ym"], account, TABLE_RESERVED, r["slot_no"], OP_STATUS,
                       detail=f"配信申請完了 → 予約枠を『{STATUS_LISTED}』に更新",
                       product_title=r.get("product_title"),
                       old_status=old, new_status=STATUS_LISTED, db_path=self.db_path)
            return {"ym": r["ym"], "slot_no": r["slot_no"], "already": False,
                    "product_title": r.get("product_title")}
        return None

    def record_trailer(self, account, title, product_id=None):
        """予告出品（配信申請）→ 出品管理表を見て当月0枠なら当月、出品済みなら翌月。

        さらに、同じ作品が『予約出品管理表』に予約出品済で載っていれば、その枠の
        ステータスを『出品済』(完了)へ自動更新する（予約→配信申請完了の反映）。
        """
        cur = current_ym()
        nxt = next_ym(cur)
        self.ensure_months([cur, nxt])
        occ_cur = self.occupied_count(cur, account, TABLE_LISTING)
        target = cur if occ_cur == 0 else nxt
        slot_no = self._empty_slot_no(target, account, TABLE_LISTING)
        if slot_no is None:
            return {"ok": False, "reason": "full", "ym": target, "account": account,
                    "table_type": TABLE_LISTING}
        when = "当月" if target == cur else "翌月"
        db.update_slot(target, account, TABLE_LISTING, slot_no,
                       checked=True, product_title=title, product_id=product_id,
                       source_op=OP_TRAILER, db_path=self.db_path)
        db.add_log(target, account, TABLE_LISTING, slot_no, OP_TRAILER,
                   detail=f"予告出品 → {when}({target}) 出品管理表 枠{slot_no}",
                   product_title=title, db_path=self.db_path)
        filled = self.occupied_count(target, account, TABLE_LISTING)
        result = {"ok": True, "ym": target, "account": account,
                  "table_type": TABLE_LISTING, "slot_no": slot_no,
                  "filled": filled, "carried_next": target == nxt, "notified": []}
        # 予約出品管理表に同一作品があれば『出品済』(完了)へ反映
        reserved = self.mark_reserved_listed(account, title)
        result["reserved_listed"] = reserved
        if reserved and not reserved.get("already"):
            msg = (f"📕 予約→配信申請完了: {account} {reserved['ym']} 予約出品管理表 "
                   f"枠{reserved['slot_no']} を『{STATUS_LISTED}』に更新\n作品: {title}")
            if self.notify(msg):
                result["notified"].append(msg)
        if target == nxt:
            msg = (f"➡️ 予告出品で翌月へ繰り上げ: {account} は当月出品済みのため "
                   f"{nxt} の出品管理表 枠{slot_no} にチェック（見落とし注意）\n作品: {title}")
            if self.notify(msg):
                result["notified"].append(msg)
        if filled >= SLOTS_PER_MONTH:
            msg = f"✅ 打ち止め: {account} が {target} の出品管理表で 3/3 到達"
            if self.notify(msg):
                result["notified"].append(msg)
        return result

    # ── 手動修正 / ステータス変更 ─────────────────────
    def set_manual(self, account, table_type, ym, slot_no, checked, product_title=None):
        self.ensure_months([ym])
        db.update_slot(ym, account, table_type, slot_no,
                       checked=checked,
                       product_title=(product_title if checked else ""),
                       source_op=OP_MANUAL, db_path=self.db_path)
        db.add_log(ym, account, table_type, slot_no, OP_MANUAL,
                   detail=f"手動で枠{slot_no}を{'ON' if checked else 'OFF'}",
                   product_title=product_title, db_path=self.db_path)
        if checked and self.occupied_count(ym, account, table_type) >= SLOTS_PER_MONTH:
            self.notify(f"✅ 打ち止め: {account} が {ym} の{TABLE_LABELS[table_type]}で "
                        f"3/3 到達（手動）")

    def set_status(self, account, table_type, ym, slot_no, status):
        self.ensure_months([ym])
        rows = db.get_slots(ym, account, table_type, self.db_path)
        old = next((r["status"] for r in rows if r["slot_no"] == slot_no), "")
        # 「出品済」「申請中」等の“使用中”ステータスを付けたら枠を占有(checked=1)にする。
        # 未予約(checked=0)の枠に出品済を付けても、占有しないと is_occupied=False で
        # 表示に出ず「設定できない」ように見えるため。空・却下は占有を変えない
        # （却下は is_occupied 側で空き扱いのまま＝自動解放）。
        occupy = bool(status) and status != STATUS_REJECTED
        db.update_slot(ym, account, table_type, slot_no, status=status,
                       checked=(True if occupy else None),
                       source_op=OP_STATUS, db_path=self.db_path)
        db.add_log(ym, account, table_type, slot_no, OP_STATUS,
                   detail=f"ステータス {old or '(なし)'} → {status or '(なし)'}",
                   old_status=old, new_status=status, db_path=self.db_path)
        # 却下 → 枠は is_occupied 上で空き扱いになる（自動解放）
        if status == STATUS_REJECTED:
            db.add_log(ym, account, table_type, slot_no, OP_STATUS,
                       detail="却下のため枠を空きに解放", db_path=self.db_path)

    # ── レポート / 抽出 ───────────────────────────────
    def fill_counts(self, ym, table_type=TABLE_LISTING):
        counts = {a: 0 for a in self.accounts()}
        for r in db.get_month_rows(ym, table_type, self.db_path):
            if is_occupied(r):
                counts[r["account"]] = counts.get(r["account"], 0) + 1
        return counts

    def accounts_zero(self, ym, table_type=TABLE_LISTING):
        return sorted([a for a, c in self.fill_counts(ym, table_type).items() if c == 0],
                      key=account_sort_key)

    @staticmethod
    def _is_unlisted_target(code, names):
        """未出品カウントの対象アカウントか。

        サークル名（accounts.name）が空、および AI-* / K-* は対象外。
        """
        code = (code or "").strip()
        if code.startswith("AI-") or code.startswith("K-"):
            return False
        return bool((names.get(code) or "").strip())

    def unlisted_slot_count(self, ym, table_type=TABLE_LISTING):
        """未出品（チェックが入っていない空き枠）の合計数を返す。

        サークル名を持つアカウントのみを対象とし、AI-*/K-* とサークル名が
        空のアカウントは対象外。却下は空き扱い（is_occupied に準拠）。
        """
        names = self.account_names()
        return sum(
            1 for r in db.get_month_rows(ym, table_type, self.db_path)
            if self._is_unlisted_target(r["account"], names) and not is_occupied(r)
        )

    def accounts_one_left(self, ym, table_type=TABLE_LISTING):
        """あと1枠だけ空き = 2枠消化済みのアカウント。"""
        return sorted([a for a, c in self.fill_counts(ym, table_type).items()
                       if c == SLOTS_PER_MONTH - 1], key=account_sort_key)

    def accounts_incomplete(self, ym, table_type=TABLE_LISTING):
        """3枠未満（未完了）= まだ3枠埋まっていないアカウント（0〜2枠）。"""
        return sorted([a for a, c in self.fill_counts(ym, table_type).items()
                       if c < SLOTS_PER_MONTH], key=account_sort_key)

    @staticmethod
    def status_color(count):
        """緑=完了 / 黄=進行中 / 赤=未着手 を返す（消化枠数ベース・後方互換用）。"""
        if count >= SLOTS_PER_MONTH:
            return "green"
        if count > 0:
            return "yellow"
        return "red"

    @staticmethod
    def account_state(rows, table_type):
        """アカウント1行の4段階ステータスを返す → (ラベル, 色キー)。

        rows: そのアカウント・その月・その表のスロット行（dict）の集まり。
        表ごとにライフサイクルが異なる:
          出品管理表(通常)     : 0枠=未着手 / 1〜2枠=進行中 / 3枠=完了
                                （作品入力した時点で完了。予約出品済は無し）
          予約出品管理表(予約) : 0枠=未着手 / 1〜2枠=進行中 /
                                3枠だが配信申請が未完=予約出品済 /
                                3枠かつ全枠 配信申請済=完了
        「配信申請済」= その枠の status が空でない（申請中/承認待ち/承認済み）。
        却下は is_occupied 上で空き扱いのため occ に含まれない。
        """
        rows = list(rows)
        occupied = [r for r in rows if is_occupied(r)]
        occ = len(occupied)
        if occ == 0:
            return STATE_NONE, STATE_COLOR[STATE_NONE]
        if occ < SLOTS_PER_MONTH:
            return STATE_WIP, STATE_COLOR[STATE_WIP]
        # 3枠すべて埋まっている
        if table_type == TABLE_RESERVED:
            # 「予約」ステータスは確保のみで配信申請済ではない → 完了に数えない
            applied = sum(1 for r in occupied
                          if r.get("status") and r.get("status") != STATUS_RESERVED)
            if applied < SLOTS_PER_MONTH:
                return STATE_RESERVED, STATE_COLOR[STATE_RESERVED]
        return STATE_DONE, STATE_COLOR[STATE_DONE]

    def pace(self, ym, table_type=TABLE_LISTING):
        """日別の消化枠数推移。checked_at の日付で集計。"""
        pace = {}
        for r in db.get_month_rows(ym, table_type, self.db_path):
            if is_occupied(r) and r.get("checked_at"):
                day = r["checked_at"][:10]
                pace[day] = pace.get(day, 0) + 1
        return dict(sorted(pace.items()))

    def daily_report_text(self, ym=None):
        """日次Discordレポート用テキスト（0枠 / あと1枠）。"""
        ym = ym or current_ym()
        zero = self.accounts_zero(ym, TABLE_LISTING)
        one = self.accounts_one_left(ym, TABLE_LISTING)
        incomplete = self.accounts_incomplete(ym, TABLE_LISTING)
        unlisted = self.unlisted_slot_count(ym, TABLE_LISTING)
        lines = [f"📊 {ym} 出品管理 日次レポート"]
        lines.append(f"■ 未出品の空き枠（対象サークルのみ）: {unlisted}枠")
        lines.append(f"■ 今月まだ0枠（未着手）: {len(zero)}件")
        lines.append("　" + ("、".join(zero) if zero else "なし"))
        lines.append(f"■ あと1枠だけ空き: {len(one)}件")
        lines.append("　" + ("、".join(one) if one else "なし"))
        lines.append(f"■ 3枠未満（未完了）: {len(incomplete)}件")
        lines.append("　" + ("、".join(incomplete) if incomplete else "なし"))
        return "\n".join(lines)

    def send_daily_report(self, ym=None):
        return self.notify(self.daily_report_text(ym))

    def maybe_send_monthly_notice(self):
        """月初に一度、先月0枠だったアカウント一覧を通知（多重送信防止）。"""
        cur = current_ym()
        last = db.get_setting(MONTHLY_NOTICE_KEY, "", self.db_path)
        if last == cur:
            return False
        pv = prev_ym(cur)
        zero = self.accounts_zero(pv, TABLE_LISTING)
        # 先月レコードが無ければ通知しない（初回起動など）
        if db.get_month_rows(pv, TABLE_LISTING, self.db_path):
            text = (f"🗓️ {pv} に未出品（0枠）だったアカウント: {len(zero)}件\n"
                    + ("、".join(zero) if zero else "なし（全アカウント出品済み）"))
            self.notify(text)
        db.set_setting(MONTHLY_NOTICE_KEY, cur, self.db_path)
        return True

    # ── 起動時のまとめ処理 ────────────────────────────
    def startup(self, profile_names=None):
        """GUI起動時に呼ぶ: アカウント同期→当月/翌月準備→月初通知。"""
        if profile_names:
            self.sync_accounts(profile_names)
        self.ensure_months()
        # 月初のDiscord通知は無効化（手動運用に変更）
