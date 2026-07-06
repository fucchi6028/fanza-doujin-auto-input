"""
各アカウントからサークル名を抽出するスクリプト
- 既存のcircle_names.jsonを読み込み、未取得のアカウントのみ処理
- --all オプションで全アカウントを再取得
"""
import json
import time
import sys
from pathlib import Path
from selenium.webdriver.common.by import By

sys.path.insert(0, str(Path(__file__).parent))
from browser.adspower import AdsPowerAPI, AdsPowerBrowser


def load_profiles_config():
    """profiles_config.jsonを読み込む"""
    config_path = Path(__file__).parent / "config" / "profiles_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_circle_names():
    """既存のcircle_names.jsonを読み込む"""
    output_path = Path(__file__).parent / "config" / "circle_names.json"
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_circle_names(results):
    """circle_names.jsonに保存"""
    output_path = Path(__file__).parent / "config" / "circle_names.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def extract_circle_name(driver):
    """作品カタログページからサークル名を取得"""
    try:
        # 作品カタログページに移動
        driver.get("https://dojin.dmm.co.jp/screening/catalog")
        time.sleep(4)

        # ページテキストを取得
        body = driver.find_element(By.TAG_NAME, "body")
        body_text = body.text
        lines = body_text.split("\n")

        # 「サークル」が単独の行で、次の行がサークル名
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # 「サークル」が単独の行（作品情報内）
            if line_stripped == "サークル":
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # 除外条件: 空行、メニュー項目など
                    if next_line and len(next_line) < 50:
                        if next_line not in ["---", "シリーズ名", "作品ID"]:
                            return next_line

        return None

    except Exception as e:
        print(f"  サークル名取得エラー: {e}", flush=True)
        return None


def main():
    """メイン処理"""
    # コマンドライン引数の処理
    fetch_all = "--all" in sys.argv

    print("=" * 60, flush=True)
    print("サークル名抽出ツール", flush=True)
    if fetch_all:
        print("モード: 全アカウント再取得", flush=True)
    else:
        print("モード: 新規アカウントのみ取得", flush=True)
    print("=" * 60, flush=True)

    # 設定読み込み
    config = load_profiles_config()
    api_url = config.get("api_url", "http://127.0.0.1:50325")
    profiles = config.get("profiles", [])

    # 既存のサークル名を読み込む
    existing_results = load_existing_circle_names()

    print(f"\n登録プロファイル数: {len(profiles)}", flush=True)
    print(f"既存取得済み: {len(existing_results)}件", flush=True)

    # 処理対象を決定
    if fetch_all:
        target_profiles = profiles
    else:
        # 未取得のプロファイルのみ
        existing_nums = set(existing_results.keys())
        target_profiles = []
        for profile in profiles:
            profile_name = profile.get("profile_name", "")
            account_num = profile_name.split("-")[-1] if "-" in profile_name else ""
            # 未取得 or 取得失敗のもの
            if account_num not in existing_nums or "取得失敗" in existing_results.get(account_num, {}).get("circle_name", ""):
                target_profiles.append(profile)

    if not target_profiles:
        print("\n新規取得対象はありません。", flush=True)
        print("全アカウントを再取得するには --all オプションを付けてください。", flush=True)
        return

    print(f"取得対象: {len(target_profiles)}件", flush=True)

    # AdsPower API接続確認
    api = AdsPowerAPI(api_url)
    connected, msg = api.check_connection()

    if not connected:
        print(f"\nAdsPowerに接続できません: {msg}", flush=True)
        print("AdsPowerを起動してから再実行してください。", flush=True)
        return

    print("AdsPower接続: OK", flush=True)

    # 結果を既存データから開始
    results = existing_results.copy()

    # 各プロファイルを処理
    for i, profile in enumerate(target_profiles):
        profile_id = profile.get("profile_id")
        profile_name = profile.get("profile_name")

        # アカウント番号を抽出
        account_num = profile_name.split("-")[-1] if "-" in profile_name else str(i + 1).zfill(3)

        print(f"\n[{i+1}/{len(target_profiles)}] {profile_name} ({profile_id})", flush=True)

        browser = AdsPowerBrowser(api)

        try:
            print("  ブラウザを起動中...", flush=True)
            if not browser.start(profile_id):
                print("  ブラウザ起動失敗", flush=True)
                results[account_num] = {
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "circle_name": "取得失敗（ブラウザ起動エラー）"
                }
                continue

            time.sleep(2)

            print("  サークル名を取得中...", flush=True)
            circle_name = extract_circle_name(browser.get_driver())

            if circle_name:
                print(f"  サークル名: {circle_name}", flush=True)
            else:
                circle_name = "取得失敗"
                print("  サークル名: 取得できませんでした", flush=True)

            results[account_num] = {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "circle_name": circle_name
            }

        except Exception as e:
            print(f"  エラー: {e}", flush=True)
            results[account_num] = {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "circle_name": f"エラー: {str(e)}"
            }

        finally:
            print("  ブラウザを終了中...", flush=True)
            browser.stop()
            time.sleep(1)

            # 途中経過を保存（中断しても途中までの結果を保持）
            save_circle_names(results)

    print(f"\n結果を保存しました: config/circle_names.json", flush=True)

    # 結果を表示
    print("\n" + "=" * 60, flush=True)
    print("結果一覧", flush=True)
    print("=" * 60, flush=True)
    print(f"\n| アカウント番号 | profile_id | サークル名 |", flush=True)
    print("|:---:|:---|:---|", flush=True)

    for account_num in sorted(results.keys()):
        data = results[account_num]
        print(f"| {account_num} | {data['profile_id']} | {data['circle_name']} |", flush=True)


if __name__ == "__main__":
    main()
