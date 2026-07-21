"""
出品管理表 / 予約出品管理表 モジュール

FANZA自動出品ツールの3操作（作品入力 / 予約作品入力 / 配信申請=予告出品）を
トリガーに、月次の出品枠（1アカウント3枠/月）を自動でチェックする管理機能。

- データの正（マスタ）は SQLite（management/management.db）
- Excel は閲覧・共有用の自動出力（openpyxl、色分け付き）
- Discord webhook 通知（設定タブで URL を登録）
"""
from management.manager import ListingManager
from management.db import (
    TABLE_LISTING,
    TABLE_RESERVED,
    STATUS_LIST,
    STATUS_REJECTED,
    normalize_account,
    account_sort_key,
)

__all__ = [
    "ListingManager",
    "TABLE_LISTING",
    "TABLE_RESERVED",
    "STATUS_LIST",
    "STATUS_REJECTED",
    "normalize_account",
    "account_sort_key",
]
