"""
AdsPower連携モジュール
AdsPowerのLocal APIを使用してブラウザを制御します
"""
import requests
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options


class AdsPowerAPI:
    """AdsPower API クライアント"""

    def __init__(self, api_url: str = "http://local.adspower.net:50325"):
        self.api_url = api_url.rstrip("/")

    def check_connection(self) -> tuple[bool, str]:
        """API接続確認"""
        try:
            response = requests.get(f"{self.api_url}/status", timeout=5)
            if response.status_code == 200:
                return True, "Connected"
            return False, f"Status: {response.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "Connection refused - AdsPower is not running"
        except Exception as e:
            return False, str(e)

    def get_groups(self) -> list[dict]:
        """グループ一覧を取得"""
        try:
            response = requests.get(
                f"{self.api_url}/api/v1/group/list",
                params={"page_size": 100},
                timeout=10
            )
            data = response.json()
            if data.get("code") == 0:
                groups = data.get("data", {}).get("list", [])
                return groups
            print(f"Group list error: {data.get('msg')}")
            return []
        except Exception as e:
            print(f"Get groups error: {e}")
            return []

    def get_profiles(self, group_id: str = None) -> list[dict]:
        """プロファイル一覧を取得（グループ指定可）"""
        try:
            params = {"page_size": 100}
            if group_id:
                params["group_id"] = group_id

            response = requests.get(
                f"{self.api_url}/api/v1/user/list",
                params=params,
                timeout=10
            )
            data = response.json()
            if data.get("code") == 0:
                profiles = data.get("data", {}).get("list", [])
                return profiles
            print(f"Profile list error: {data.get('msg')}")
            return []
        except Exception as e:
            print(f"Get profiles error: {e}")
            return []

    def start_browser(self, profile_id: str) -> tuple[bool, dict]:
        """ブラウザを起動"""
        try:
            response = requests.get(
                f"{self.api_url}/api/v1/browser/start",
                params={"user_id": profile_id},
                timeout=30
            )
            data = response.json()

            if data.get("code") == 0:
                return True, data.get("data", {})
            return False, {"error": data.get("msg", "Unknown error")}
        except Exception as e:
            return False, {"error": str(e)}

    def stop_browser(self, profile_id: str) -> bool:
        """ブラウザを終了"""
        try:
            response = requests.get(
                f"{self.api_url}/api/v1/browser/stop",
                params={"user_id": profile_id},
                timeout=10
            )
            data = response.json()
            return data.get("code") == 0
        except Exception as e:
            print(f"Stop browser error: {e}")
            return False

    def check_browser_status(self, profile_id: str) -> tuple[bool, dict]:
        """ブラウザの状態を確認"""
        try:
            response = requests.get(
                f"{self.api_url}/api/v1/browser/active",
                params={"user_id": profile_id},
                timeout=10
            )
            data = response.json()
            if data.get("code") == 0:
                return True, data.get("data", {})
            return False, {}
        except Exception as e:
            return False, {"error": str(e)}


class AdsPowerBrowser:
    """AdsPowerブラウザ管理クラス"""

    def __init__(self, api: AdsPowerAPI = None):
        self.api = api or AdsPowerAPI()
        self.driver = None
        self.profile_id = None

    def start(self, profile_id: str) -> bool:
        """指定したプロファイルでブラウザを起動してSeleniumで接続"""
        success, data = self.api.start_browser(profile_id)

        if not success:
            print(f"Browser start failed: {data.get('error')}")
            return False

        try:
            # WebDriver接続情報を取得
            ws_endpoint = data["ws"]["selenium"]
            webdriver_path = data["webdriver"]

            # Seleniumで接続
            options = Options()
            options.add_experimental_option(
                "debuggerAddress",
                ws_endpoint.replace("ws://", "").split("/")[0]
            )

            service = Service(executable_path=webdriver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            self.profile_id = profile_id

            print(f"Browser connected: {profile_id}")
            return True

        except Exception as e:
            print(f"Selenium connection error: {e}")
            return False

    def stop(self):
        """ブラウザを終了"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None

        if self.profile_id:
            self.api.stop_browser(self.profile_id)
            self.profile_id = None

    def navigate_to(self, url: str):
        """指定URLに移動"""
        if self.driver:
            self.driver.get(url)
            time.sleep(2)

    def get_driver(self):
        """WebDriverインスタンスを取得"""
        return self.driver


# テスト用
if __name__ == "__main__":
    api = AdsPowerAPI()

    # 接続確認
    connected, msg = api.check_connection()
    print(f"Connection: {connected} - {msg}")

    if connected:
        # グループ一覧
        groups = api.get_groups()
        print(f"\nGroups ({len(groups)}):")
        for g in groups:
            print(f"  - {g.get('group_id')}: {g.get('group_name')}")

        # プロファイル一覧
        profiles = api.get_profiles()
        print(f"\nProfiles ({len(profiles)}):")
        for p in profiles[:5]:
            print(f"  - {p.get('user_id')}: {p.get('name')}")
