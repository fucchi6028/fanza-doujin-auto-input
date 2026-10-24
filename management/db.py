"""
出品管理表の SQLite 永続化レイヤ（低レベルアクセス）

設計方針:
- 自動化スクリプトは複数スレッド（ADSPower操作スレッド等）から書き込むため、
  接続はメソッド呼び出しごとに開いて閉じる（connection-per-call）。
  これによりスレッド間で接続を共有せず、ロック競合を避ける。
- WAL + busy_timeout で軽い競合はリトライ吸収。
- Excel は別途エクスポートするだけで、DB を正とする。
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# ── 定数 ────────────────────────────────────────────────
DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "management.db"

TABLE_LISTING = "listing"     # 出品管理表（通常出品）
TABLE_RESERVED = "reserved"   # 予約出品管理表（予約出品）
TABLE_SALES = "sales_daily"   # 日別売上（卸金額）

SLOTS_PER_MONTH = 3           # 1アカウント3作品/月

# 配信申請ステータス
STATUS_LISTED = "出品済"        # 予約出品→配信申請まで済んだ枠（＝完了）
STATUS_APPLYING = "申請中"
STATUS_WAITING = "承認待ち"
STATUS_APPROVED = "承認済み"
STATUS_REJECTED = "却下"
STATUS_LIST = ["", STATUS_LISTED, STATUS_APPLYING,
               STATUS_WAITING, STATUS_APPROVED, STATUS_REJECTED]

# 操作種別（ログ用）
OP_LISTING = "作品入力"
OP_RESERVED = "予約作品入力"
OP_TRAILER = "予告出品"
OP_MANUAL = "手動修正"
OP_STATUS = "ステータス変更"
OP_ROLLOVER = "月末繰越"

# 管理対象の基準アカウント（プロファイルが未登録でも表を完成させるため）
CANONICAL_ACCOUNTS = [f"{i:03d}" for i in range(1, 101)] + ["K-01", "K-02"]


# ── アカウントコード正規化 ──────────────────────────────
def normalize_account(profile_name: str) -> str:
    """ADSPowerのprofile_nameを管理表のアカウントコードへ正規化する。

    FANZA-001..FANZA-100      -> '001'..'100'
    FANZA-AI-001..FANZA-AI-00N -> 'AI-001'..
    K-01, K-02                -> そのまま
    それ以外                   -> 前後空白を除いたそのまま
    """
    name = (profile_name or "").strip()
    m = re.match(r"^FANZA-AI-(\d+)$", name, re.IGNORECASE)
    if m:
        return f"AI-{int(m.group(1)):03d}"
    m = re.match(r"^FANZA-(\d+)$", name, re.IGNORECASE)
    if m:
        return f"{int(m.group(1)):03d}"
    return name


def account_sort_key(code: str):
    """アカウントコードの表示ソートキー: 数字 → AI-### → K-## → その他。"""
    code = (code or "").strip()
    if re.fullmatch(r"\d+", code):
        return (0, int(code), "")
    m = re.fullmatch(r"AI-(\d+)", code)
    if m:
        return (1, int(m.group(1)), "")
    m = re.fullmatch(r"K-(\d+)", code)
    if m:
        return (2, int(m.group(1)), "")
    return (3, 0, code)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 接続 ────────────────────────────────────────────────
@contextmanager
def connect(db_path=None):
    """接続を1回のトランザクション単位で開閉するコンテキストマネージャ。"""
    path = str(db_path or DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """スキーマを作成し、基準アカウントを投入する。"""
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                code   TEXT PRIMARY KEY,
                name   TEXT,               -- サークル名（表示用アカウント名）
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS slots (
                ym            TEXT    NOT NULL,   -- 'YYYY-MM'
                account       TEXT    NOT NULL,
                table_type    TEXT    NOT NULL,   -- 'listing' | 'reserved'
                slot_no       INTEGER NOT NULL,   -- 1..3
                checked       INTEGER NOT NULL DEFAULT 0,
                product_title TEXT,
                product_id    TEXT,
                status        TEXT    NOT NULL DEFAULT '',
                source_op     TEXT,
                checked_at    TEXT,
                updated_at    TEXT,
                PRIMARY KEY (ym, account, table_type, slot_no)
            );

            CREATE TABLE IF NOT EXISTS logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT    NOT NULL,
                ym            TEXT,
                account       TEXT,
                table_type    TEXT,
                slot_no       INTEGER,
                operation     TEXT,
                detail        TEXT,
                product_title TEXT,
                old_status    TEXT,
                new_status    TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- 日別売上（卸金額）。FANZA売上ページから1日1回取り込む。
            -- amount は「卸金額（サークル取り分）」の円単位整数。
            CREATE TABLE IF NOT EXISTS sales_daily (
                sale_date  TEXT    NOT NULL,   -- 'YYYY-MM-DD'（売上が発生した日）
                account    TEXT    NOT NULL,   -- アカウントコード（'001' 等）
                amount     INTEGER NOT NULL,   -- 卸金額（円）
                fetched_at TEXT,               -- 取得日時
                PRIMARY KEY (sale_date, account)
            );

            -- 月累計の卸金額。日次分の足し算ではなく、FANZAの売上ページから
            -- 「月初〜対象日」の期間で直接取得した値を保存する。途中から使い
            -- 始めた場合やFANZA側の遡り修正にも追従できる。
            CREATE TABLE IF NOT EXISTS sales_month (
                ym         TEXT    NOT NULL,   -- 'YYYY-MM'
                account    TEXT    NOT NULL,
                amount     INTEGER NOT NULL,   -- 卸金額の月累計（円）
                as_of_date TEXT,               -- どの日までの累計か
                fetched_at TEXT,
                PRIMARY KEY (ym, account)
            );

            -- 実際の振込額。FANZAの「支払情報の確認」ページから取り込む。
            -- 売上（卸金額）は月末締め・翌月20日（休日なら翌平日）払いで、
            -- 振込額は卸金額から手数料等が引かれた金額になる。
            CREATE TABLE IF NOT EXISTS sales_payment (
                sales_ym   TEXT    NOT NULL,   -- 売上の対象月 'YYYY-MM'（8月分なら'2026-08'）
                account    TEXT    NOT NULL,
                pay_date   TEXT,               -- 振込日 'YYYY-MM-DD'
                amount     INTEGER NOT NULL,   -- 支払金額合計（円）
                carry_over INTEGER NOT NULL DEFAULT 0,  -- 次月繰越（円）
                status     TEXT,               -- 支払結果（'支払い完了' 等）
                fetched_at TEXT,
                PRIMARY KEY (sales_ym, account)
            );

            CREATE INDEX IF NOT EXISTS idx_slots_ym_type
                ON slots (ym, table_type);
            CREATE INDEX IF NOT EXISTS idx_slots_title
                ON slots (product_title);
            CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs (ts);
            CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_daily (sale_date);
            """
        )
        # 既存DBへの name 列マイグレーション（無ければ追加）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
        if "name" not in cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN name TEXT")
        for code in CANONICAL_ACCOUNTS:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (code, active) VALUES (?, 1)", (code,)
            )


# ── settings ────────────────────────────────────────────
def get_setting(key: str, default=None, db_path=None):
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, db_path=None):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ── accounts ────────────────────────────────────────────
def upsert_account(code: str, active: int = 1, db_path=None):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts (code, active) VALUES (?, ?) "
            "ON CONFLICT(code) DO UPDATE SET active=excluded.active",
            (code, active),
        )


def set_account_name(code: str, name: str, db_path=None):
    """アカウントのサークル名（表示名）を設定。空文字なら上書きしない。"""
    if not name:
        return
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts (code, name, active) VALUES (?, ?, 1) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name",
            (code, name),
        )


def account_names(db_path=None):
    """{code: name} を返す（name未設定はキーに含めるが値は空文字）。"""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT code, name FROM accounts").fetchall()
    return {r["code"]: (r["name"] or "") for r in rows}


def list_accounts(db_path=None):
    with connect(db_path) as conn:
        rows = conn.execute("SELECT code FROM accounts WHERE active=1").fetchall()
    codes = [r["code"] for r in rows]
    codes.sort(key=account_sort_key)
    return codes


# ── slots ───────────────────────────────────────────────
def is_occupied(row) -> bool:
    """枠が埋まっているか。却下は空きとして扱う（枠の自動解放）。"""
    return bool(row["checked"]) and row["status"] != STATUS_REJECTED


def ensure_month(ym: str, accounts, db_path=None) -> bool:
    """指定年月・全アカウントの3枠×2表を（無ければ）作成する。

    新規に当月/翌月のレコードを作った場合 True を返す（繰越判定用）。
    """
    created = False
    ts = now_iso()
    with connect(db_path) as conn:
        for account in accounts:
            for table_type in (TABLE_LISTING, TABLE_RESERVED):
                for slot_no in range(1, SLOTS_PER_MONTH + 1):
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO slots "
                        "(ym, account, table_type, slot_no, checked, status, updated_at) "
                        "VALUES (?, ?, ?, ?, 0, '', ?)",
                        (ym, account, table_type, slot_no, ts),
                    )
                    if cur.rowcount:
                        created = True
    return created


def get_slots(ym: str, account: str, table_type: str, db_path=None):
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE ym=? AND account=? AND table_type=? "
            "ORDER BY slot_no",
            (ym, account, table_type),
        ).fetchall()
    return [dict(r) for r in rows]


def get_account_slots(account: str, table_type: str, db_path=None):
    """指定アカウント・表の全月の枠を返す（月をまたぐタイトル突合に使う）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE account=? AND table_type=? "
            "ORDER BY ym, slot_no",
            (account, table_type),
        ).fetchall()
    return [dict(r) for r in rows]


def get_month_rows(ym: str, table_type: str, db_path=None):
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE ym=? AND table_type=? "
            "ORDER BY account, slot_no",
            (ym, table_type),
        ).fetchall()
    return [dict(r) for r in rows]


def update_slot(ym, account, table_type, slot_no, *, checked=None,
                product_title=None, product_id=None, status=None,
                source_op=None, db_path=None):
    """1枠を更新する（None のフィールドは変更しない）。"""
    sets, params = [], []
    if checked is not None:
        sets.append("checked=?")
        params.append(1 if checked else 0)
        sets.append("checked_at=?")
        params.append(now_iso() if checked else None)
    if product_title is not None:
        sets.append("product_title=?")
        params.append(product_title or None)
    if product_id is not None:
        sets.append("product_id=?")
        params.append(product_id or None)
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if source_op is not None:
        sets.append("source_op=?")
        params.append(source_op)
    sets.append("updated_at=?")
    params.append(now_iso())
    params.extend([ym, account, table_type, slot_no])
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE slots SET {', '.join(sets)} "
            "WHERE ym=? AND account=? AND table_type=? AND slot_no=?",
            params,
        )


def add_log(ym, account, table_type, slot_no, operation, detail="",
            product_title=None, old_status=None, new_status=None, db_path=None):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO logs "
            "(ts, ym, account, table_type, slot_no, operation, detail, "
            " product_title, old_status, new_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), ym, account, table_type, slot_no, operation, detail,
             product_title, old_status, new_status),
        )


def query_logs(limit=200, account=None, ym=None, db_path=None):
    where, params = [], []
    if account:
        where.append("account=?")
        params.append(account)
    if ym:
        where.append("ym=?")
        params.append(ym)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM logs {clause} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── sales_daily（日別売上・卸金額） ──────────────────────
def upsert_sales(sale_date: str, account: str, amount: int, db_path=None):
    """1アカウント・1日分の卸金額を登録（再取得したら上書き）。"""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sales_daily (sale_date, account, amount, fetched_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(sale_date, account) DO UPDATE SET "
            "amount=excluded.amount, fetched_at=excluded.fetched_at",
            (sale_date, account, int(amount), now_iso()),
        )


def get_sales_day(sale_date: str, db_path=None) -> dict:
    """{account: amount} を返す（その日に記録が無ければ空dict）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT account, amount FROM sales_daily WHERE sale_date=?", (sale_date,)
        ).fetchall()
    return {r["account"]: r["amount"] for r in rows}


def get_sales_month(ym: str, db_path=None) -> dict:
    """指定年月('YYYY-MM')のアカウント別累計 {account: amount} を返す。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT account, SUM(amount) AS total FROM sales_daily "
            "WHERE substr(sale_date, 1, 7)=? GROUP BY account",
            (ym,),
        ).fetchall()
    return {r["account"]: r["total"] for r in rows}


def sales_recorded_accounts(sale_date: str, db_path=None) -> set:
    """その日の取得が済んでいるアカウントコードの集合。"""
    return set(get_sales_day(sale_date, db_path).keys())


def get_sales_days_in_month(ym: str, db_path=None) -> dict:
    """指定年月の日別合計 {'YYYY-MM-DD': amount} を返す（日次推移の確認用）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT sale_date, SUM(amount) AS total FROM sales_daily "
            "WHERE substr(sale_date, 1, 7)=? GROUP BY sale_date ORDER BY sale_date",
            (ym,),
        ).fetchall()
    return {r["sale_date"]: r["total"] for r in rows}


def upsert_sales_month(ym: str, account: str, amount: int, as_of_date: str, db_path=None):
    """FANZAの売上ページから取得した月累計を保存（同じ月なら上書き）。"""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sales_month (ym, account, amount, as_of_date, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(ym, account) DO UPDATE SET "
            "amount=excluded.amount, as_of_date=excluded.as_of_date, "
            "fetched_at=excluded.fetched_at "
            # 過去日を指定して再実行しても、より新しい時点の累計を巻き戻さない
            "WHERE excluded.as_of_date >= sales_month.as_of_date",
            (ym, account, int(amount), as_of_date, now_iso()),
        )


def get_sales_month_reported(ym: str, db_path=None) -> dict:
    """FANZA由来の月累計 {account: amount} を返す。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT account, amount FROM sales_month WHERE ym=?", (ym,)
        ).fetchall()
    return {r["account"]: r["amount"] for r in rows}


# ── sales_payment（実際の振込額） ─────────────────────────
def upsert_sales_payment(sales_ym: str, account: str, pay_date: str, amount: int,
                         carry_over: int = 0, status: str = "", db_path=None):
    """1アカウント・1ヶ月分の振込実績を保存（再取得したら上書き）。"""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sales_payment "
            "(sales_ym, account, pay_date, amount, carry_over, status, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sales_ym, account) DO UPDATE SET "
            "pay_date=excluded.pay_date, amount=excluded.amount, "
            "carry_over=excluded.carry_over, status=excluded.status, "
            "fetched_at=excluded.fetched_at",
            (sales_ym, account, pay_date, int(amount), int(carry_over or 0),
             status or "", now_iso()),
        )


def get_sales_payments(sales_ym: str = None, db_path=None) -> list:
    """振込実績の行（dict）を返す。sales_ym を指定するとその月分だけ。"""
    sql = "SELECT * FROM sales_payment"
    params = ()
    if sales_ym:
        sql += " WHERE sales_ym=?"
        params = (sales_ym,)
    with connect(db_path) as conn:
        rows = conn.execute(sql + " ORDER BY sales_ym, account", params).fetchall()
    return [dict(r) for r in rows]
