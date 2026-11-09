"""
AdsPower連携モジュール
AdsPowerのLocal APIを使用してブラウザを制御します
"""
import os
import socket
import subprocess
import requests
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options


# AdsPower未登録のプロファイル（ローカルChromeで動かすもの）を識別する接頭辞。
# profiles_config.json の profile_id をこの接頭辞にしておくと、AdsPowerではなく
# 通常のChrome（LocalBrowser）で作品入力・予約作品入力・配信申請が動く。
LOCAL_PROFILE_PREFIX = "LOCAL-"


def is_local_profile(profile_id: str) -> bool:
    """AdsPowerを介さずローカルChromeで動かすプロファイルか判定する"""
    return bool(profile_id) and str(profile_id).startswith(LOCAL_PROFILE_PREFIX)


class AdsPowerAPI:
    """AdsPower API クライアント"""

    def __init__(self, api_url: str = "http://127.0.0.1:50325", api_key: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        """AdsPower 8.x 以降はLocal APIにもAPIキー認証が必要（設定→APIで発行）"""
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def check_connection(self) -> tuple[bool, str]:
        """API接続確認"""
        try:
            response = requests.get(f"{self.api_url}/status", timeout=5, headers=self._headers())
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
            response = requests.get(
                f"{self.api_url}{endpoint}", params=params, timeout=10, headers=self._headers()
            )
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

    def start_browser(self, profile_id: str, headless: bool = False) -> tuple[bool, dict]:
        """ブラウザを起動（headless=True でウィンドウを表示せずに起動）"""
        try:
            params = {"user_id": profile_id}
            if headless:
                params["headless"] = "1"
            response = requests.get(
                f"{self.api_url}/api/v1/browser/start",
                params=params,
                timeout=30,
                headers=self._headers()
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
                timeout=10,
                headers=self._headers()
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
                timeout=10,
                headers=self._headers()
            )
            data = response.json()
            if data.get("code") == 0:
                return True, data.get("data", {})
            return False, {}
        except Exception as e:
            return False, {"error": str(e)}


class AdsPowerBrowser:
    """AdsPowerブラウザ管理クラス"""

    def __init__(self, api: AdsPowerAPI = None, headless: bool = False):
        self.api = api or AdsPowerAPI()
        self.driver = None
        self.profile_id = None
        self.last_error = ""  # 直近の起動失敗理由（GUI表示・診断用）
        self.headless = headless  # True でウィンドウを出さずに起動（定時実行用）

    def _connect_once(self, profile_id: str) -> bool:
        """1回分: AdsPower起動→Selenium接続。失敗時は self.last_error に理由を残す。"""
        success, data = self.api.start_browser(profile_id, headless=self.headless)
        if not success:
            self.last_error = f"AdsPower起動API失敗: {data.get('error')}"
            return False

        try:
            ws_endpoint = data["ws"]["selenium"]
            webdriver_path = data["webdriver"]
        except (KeyError, TypeError):
            self.last_error = f"AdsPower応答に接続情報がありません: {data}"
            return False

        # AdsPowerがChromeカーネル更新直後などで chromedriver がまだ用意されていないケース
        if not webdriver_path or not os.path.exists(webdriver_path):
            self.last_error = (
                f"chromedriverが見つかりません（AdsPowerの更新中/準備未完了の可能性）: {webdriver_path}"
            )
            return False

        try:
            options = Options()
            options.add_experimental_option(
                "debuggerAddress",
                ws_endpoint.replace("ws://", "").split("/")[0]
            )
            service = Service(executable_path=webdriver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            self.profile_id = profile_id
            self.last_error = ""
            print(f"Browser connected: {profile_id}")
            return True
        except Exception as e:
            self.last_error = f"Selenium接続エラー: {e}"
            print(self.last_error)
            return False

    def start(self, profile_id: str, retries: int = 2) -> bool:
        """指定したプロファイルでブラウザを起動してSeleniumで接続（一過性失敗はリトライ）"""
        self.last_error = ""
        for attempt in range(retries + 1):
            if self._connect_once(profile_id):
                return True
            print(f"Browser start failed (attempt {attempt + 1}/{retries + 1}): {self.last_error}")
            if attempt < retries:
                time.sleep(2.0)  # 更新直後・レート制限などの一過性失敗を待って再試行
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


class LocalBrowser:
    """AdsPower未登録プロファイル用の通常Chrome管理クラス。

    AdsPowerBrowser と同じインターフェイス（start/stop/get_driver/navigate_to/last_error）
    を持つので、呼び出し側は生成するクラスを差し替えるだけで使える。

    重要: Chrome を chromedriver 経由で起動すると自動化フラグ（navigator.webdriver 等）が
    立ち、Googleログインで「このブラウザは安全ではない可能性があります」と弾かれる。
    そこで Chrome は **通常のプロセスとして** remote-debugging-port 付きで起動し、
    Selenium は後からデバッグポートに接続するだけにする。これなら自動化検知されず
    ユーザーが普通にログインできる。

    ・プロファイルごとに永続 user-data-dir を使うため、一度FANZAにログインすれば
      次回以降もログイン状態が保持される。
    ・固定のremote-debugging-portで起動し、次回以降は起動済みChromeへ再接続する。
    """

    # ローカルChromeの永続プロファイル置き場（プロファイルIDごとにサブフォルダ）
    PROFILE_ROOT = Path(__file__).parent / "local_profiles"

    # Chrome実行ファイルの候補（Windows）
    CHROME_CANDIDATES = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            r"Google\Chrome\Application\chrome.exe",
        ),
    ]

    def __init__(self, api=None):
        # api は互換のため受け取るが未使用（AdsPowerBrowser とシグネチャを合わせる）
        self.driver = None
        self.profile_id = None
        self.last_error = ""

    def _user_data_dir(self, profile_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_id)
        return self.PROFILE_ROOT / safe

    def _debug_port(self, profile_id: str) -> int:
        """プロファイルIDから決定的にデバッグポートを算出（9300〜9499）"""
        return 9300 + (sum(profile_id.encode("utf-8")) % 200)

    def _find_chrome(self) -> str:
        """chrome.exe のパスを探す。見つからなければ空文字。"""
        for path in self.CHROME_CANDIDATES:
            if path and os.path.exists(path):
                return path
        return ""

    def _port_open(self, port: int) -> bool:
        """デバッグポートが接続可能か（Chromeが起動済みか）を確認する"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _attach(self, port: int):
        """起動済みChrome（同一デバッグポート）へSeleniumで接続する。失敗時はNone。"""
        try:
            options = Options()
            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
            # Selenium Manager が chromedriver を自動解決する
            return webdriver.Chrome(options=options)
        except Exception as e:
            self.last_error = f"デバッグポート接続エラー: {e}"
            return None

    def _spawn_chrome(self, profile_id: str, port: int) -> bool:
        """Chromeを通常プロセスとして起動（chromedriverを介さない＝自動化検知されない）。"""
        chrome = self._find_chrome()
        if not chrome:
            self.last_error = (
                "chrome.exe が見つかりません。Google Chrome をインストールしてください。"
            )
            return False

        user_data_dir = self._user_data_dir(profile_id)
        user_data_dir.mkdir(parents=True, exist_ok=True)

        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            # ツールが終了してもChromeは残す（手動確認・申請のため）
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            self.last_error = f"Chrome起動エラー: {e}"
            return False

        # デバッグポートが開くまで待機（最大約15秒）
        for _ in range(30):
            if self._port_open(port):
                return True
            time.sleep(0.5)
        self.last_error = "Chromeのデバッグポートが開きませんでした（起動失敗の可能性）"
        return False

    def start(self, profile_id: str, retries: int = 2) -> bool:
        """通常Chromeを起動（または起動済みへ接続）してSeleniumで制御する"""
        self.last_error = ""
        port = self._debug_port(profile_id)

        # すでにChromeがデバッグポートで動いていれば、それに接続して再利用
        if not self._port_open(port):
            if not self._spawn_chrome(profile_id, port):
                return False

        for attempt in range(retries + 1):
            driver = self._attach(port)
            if driver is not None:
                self.driver = driver
                self.profile_id = profile_id
                self.last_error = ""
                print(f"Local browser attached: {profile_id} (port {port})")
                return True
            print(f"Local browser attach failed "
                  f"(attempt {attempt + 1}/{retries + 1}): {self.last_error}")
            if attempt < retries:
                time.sleep(1.5)
        return False

    def stop(self):
        """Seleniumの接続を切る（detach指定のためChrome自体は開いたまま）"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
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
    import json
    from pathlib import Path

    # 保存済み設定（config/profiles_config.json）からURLとAPIキーを読む
    cfg_path = Path(__file__).parent.parent / "config" / "profiles_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    api = AdsPowerAPI(cfg.get("api_url", "http://127.0.0.1:50325"), cfg.get("api_key", ""))

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
