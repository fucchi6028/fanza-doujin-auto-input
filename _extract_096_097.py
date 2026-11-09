"""096 / 097 のサークル名だけを取得して circle_names.json に反映する。"""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from browser.adspower import AdsPowerAPI, AdsPowerBrowser
from extract_circle_names import (
    load_profiles_config,
    load_existing_circle_names,
    save_circle_names,
    extract_circle_name,
)

TARGET_SUFFIXES = ("096", "097")


def main():
    config = load_profiles_config()
    api_url = config.get("api_url", "http://127.0.0.1:50325")
    profiles = config.get("profiles", [])

    targets = [
        p for p in profiles
        if str(p.get("profile_name", "")).endswith(TARGET_SUFFIXES)
    ]
    print(f"取得対象: {[p.get('profile_name') for p in targets]}", flush=True)
    if not targets:
        print("対象プロファイルが見つかりません。", flush=True)
        return

    api = AdsPowerAPI(api_url)
    connected, msg = api.check_connection()
    if not connected:
        print(f"AdsPowerに接続できません: {msg}", flush=True)
        return
    print("AdsPower接続: OK", flush=True)

    results = load_existing_circle_names()

    for i, profile in enumerate(targets):
        profile_id = profile.get("profile_id")
        profile_name = profile.get("profile_name")
        account_num = profile_name.split("-")[-1]
        print(f"\n[{i+1}/{len(targets)}] {profile_name} ({profile_id})", flush=True)

        browser = AdsPowerBrowser(api)
        try:
            print("  ブラウザを起動中...", flush=True)
            if not browser.start(profile_id):
                print("  ブラウザ起動失敗", flush=True)
                results[account_num] = {
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "circle_name": "取得失敗（ブラウザ起動エラー）",
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
                "circle_name": circle_name,
            }
        except Exception as e:
            print(f"  エラー: {e}", flush=True)
            results[account_num] = {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "circle_name": f"エラー: {str(e)}",
            }
        finally:
            print("  ブラウザを終了中...", flush=True)
            browser.stop()
            time.sleep(1)
            save_circle_names(results)

    print("\n反映結果:", flush=True)
    for num in TARGET_SUFFIXES:
        d = results.get(num)
        print(f"  {num}: {d.get('circle_name') if d else '(なし)'}", flush=True)


if __name__ == "__main__":
    main()
