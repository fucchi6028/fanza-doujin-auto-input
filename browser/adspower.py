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

    def __init__(self, api_url: str = "http://127.0.0.1:50325"):
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

    PAGE_SIZE = 100      # AdsPower API の最大 page_size
    RATE_LIMIT_SEC = 1.1 # AdsPower Local API: 1 req/sec 制限 + マージン

    def _request_page(self, endpoint: str, params: dict, max_retries: int = 4) -> dict:
        """1ページ分のAPI呼び出し（レート制限リトライ付き）。失敗時は最後の data を返す。"""
        last_data: dict = {}
        backoff = self.RATE_LIMIT_SEC
        for attempt in range(max_retries):
            response = requests.get(f"{self.api_url}{endpoint}", params=params, timeout=10)
            last_data = response.json() or {}
            if last_data.get("code") == 0:
                return last_data
            msg = (last_data.get("msg") or "").lower()
            # レート制限らしきメッセージならバックオフして再試行
            if any(k in msg for k in ("too many", "frequent", "请求", "rate")):
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            # それ以外のエラーはそのまま返す
            return last_data
        return last_data

    def _fetch_all_pages(self, endpoint: str, extra_params: dict = None) -> list[dict]:
        """AdsPower のリスト系エンドポイントを全ページ取得"""
        results: list[dict] = []
        page = 1
        while True:
            params = {"page": page, "page_size": self.PAGE_SIZE}
            if extra_params:
                params.update(extra_params)

            # 2ページ目以降はレート制限回避のため必ず待機
            if page > 1:
                time.sleep(self.RATE_LIMIT_SEC)

            data = self._request_page(endpoint, params)
            if data.get("code") != 0:
                print(f"{endpoint} error (page {page}): {data.get('msg')}")
                break

            payload = data.get("data", {}) or {}
            items = payload.get("list", []) or []
            results.extend(items)

            # 終了判定: 返却件数が page_size 未満、または total に到達
            if len(items) < self.PAGE_SIZE:
                break
            total = payload.get("total")
            if isinstance(total, int) and len(results) >= total:
                break

            page += 1
            if page > 100:  # 安全弁
                print(f"{endpoint}: pagination safety break at page {page}")
                break

        return results

    def get_groups(self) -> list[dict]:
        """グループ一覧を全件取得"""
        try:
            return self._fetch_all_pages("/api/v1/group/list")
        except Exception as e:
            print(f"Get groups error: {e}")
            return []

    def get_profiles(self, group_id: str = None) -> list[dict]:
        """プロファイル一覧を全件取得（グループ指定可）"""
        try:
            extra = {"group_id": group_id} if group_id else None
            return self._fetch_all_pages("/api/v1/user/list", extra)
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
