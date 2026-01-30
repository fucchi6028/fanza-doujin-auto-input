"""
FANZA同人 フォーム自動入力モジュール
"""
import time
import sys
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# パス設定
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.keywords_data import POPULAR_KEYWORDS, get_keyword_name


class FanzaFormFiller:
    """FANZA同人フォーム自動入力クラス"""

    FORM_URL = "https://dojin.dmm.co.jp/addproduct"

    def __init__(self, driver, callback=None):
        """
        Args:
            driver: Selenium WebDriver
            callback: 進捗報告用コールバック関数 (message: str) -> None
        """
        self.driver = driver
        self.wait = WebDriverWait(driver, 15)
        self.callback = callback or (lambda msg: print(msg))

    def _report(self, message: str):
        """進捗を報告"""
        self.callback(message)

    def navigate_to_form(self):
        """登録フォームページに移動"""
        self._report("フォームページを開いています...")
        self.driver.get(self.FORM_URL)
        time.sleep(3)
        self._report("フォームページを開きました")

    def _click_radio(self, name: str, value: str):
        """ラジオボタンを選択"""
        try:
            # 直接ラジオボタンをクリック
            radio = self.driver.find_element(By.CSS_SELECTOR, f"input[name='{name}'][value='{value}']")
            self.driver.execute_script("arguments[0].click();", radio)
            time.sleep(0.5)
            return True
        except Exception as e:
            # ラジオボタンが見つからない場合はラベルを試す
            try:
                label = self.driver.find_element(
                    By.CSS_SELECTOR, f"label[for='{name}--{value}']"
                )
                self.driver.execute_script("arguments[0].click();", label)
                time.sleep(0.5)
                return True
            except:
                self._report(f"ラジオボタン選択エラー ({name}={value}): {e}")
                return False

    def _fill_input(self, name: str, value: str):
        """入力フィールドに値を設定"""
        try:
            input_elem = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"input[name='{name}']"))
            )
            input_elem.clear()
            input_elem.send_keys(value)
            time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"入力エラー ({name}): {e}")
            return False

    def _fill_textarea(self, name: str, value: str):
        """テキストエリアに値を設定"""
        try:
            textarea = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"textarea[name='{name}']"))
            )
            textarea.clear()
            textarea.send_keys(value)
            time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"テキストエリア入力エラー ({name}): {e}")
            return False

    def _click_checkbox_by_value(self, name: str, value: str, should_check: bool = True):
        """チェックボックスを値で設定"""
        try:
            checkbox = self.driver.find_element(
                By.CSS_SELECTOR, f"input[name='{name}'][value='{value}']"
            )
            is_checked = checkbox.is_selected()
            if is_checked != should_check:
                self.driver.execute_script("arguments[0].click();", checkbox)
                time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"チェックボックス設定エラー ({name}={value}): {e}")
            return False

    def _select_dropdown(self, name: str, value: str):
        """セレクトボックスの値を選択"""
        try:
            select_elem = self.driver.find_element(By.CSS_SELECTOR, f"select[name='{name}']")
            # optionを選択
            option = select_elem.find_element(By.CSS_SELECTOR, f"option[value='{value}']")
            self.driver.execute_script("arguments[0].selected = true;", option)
            # changeイベントを発火
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                select_elem
            )
            time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"セレクト選択エラー ({name}={value}): {e}")
            return False

    def _click_category_tab(self, category_name: str):
        """カテゴリタブをクリック"""
        try:
            # HTML: <ul class="tab-keyword"><li>シチュエーション/系統</li><li>キャラクター</li>...</ul>
            tabs = self.driver.find_elements(By.CSS_SELECTOR, "ul.tab-keyword li")
            for tab in tabs:
                if tab.text.strip() == category_name:
                    self.driver.execute_script("arguments[0].click();", tab)
                    time.sleep(0.5)  # タブ切り替え待ち
                    return True
            return False
        except Exception:
            return False

    def _click_keyword_by_name_in_current_tab(self, keyword_name: str):
        """現在のタブ内でキーワード名でチェックボックスを選択"""
        try:
            # HTML: <li><input class="form-keywords" ...><label for="...">アナル</label></li>
            labels = self.driver.find_elements(By.CSS_SELECTOR, "ul.group li label")
            for label in labels:
                if label.text.strip() == keyword_name:
                    # labelのfor属性からinputのIDを取得
                    for_id = label.get_attribute("for")
                    if for_id:
                        checkbox = self.driver.find_element(By.ID, for_id)
                        if not checkbox.is_selected():
                            self.driver.execute_script("arguments[0].click();", checkbox)
                            time.sleep(0.3)
                        return True
            return False
        except Exception:
            return False

    def _click_keyword_by_name(self, keyword_name: str):
        """キーワード名で選択（全カテゴリタブを順番に検索）"""
        # カテゴリタブの名前リスト
        category_tab_names = [
            "シチュエーション/系統",
            "キャラクター",
            "タイプ/身体的特徴",
            "コスチューム/服装/アイテム",
            "プレイ",
            "その他オプション",
        ]

        for cat_name in category_tab_names:
            # カテゴリタブをクリック
            if self._click_category_tab(cat_name):
                # 現在のタブ内でキーワードを探す
                if self._click_keyword_by_name_in_current_tab(keyword_name):
                    return True

        return False

    def _click_popular_keyword(self, keyword_name: str):
        """人気キーワードをテキストで選択"""
        try:
            # HTML: <li class="keyword-recommend-tag"><span class="keyword-recommend-tag-text">おっぱい</span></li>
            popular_tags = self.driver.find_elements(
                By.CSS_SELECTOR, "li.keyword-recommend-tag"
            )
            for tag in popular_tags:
                try:
                    span = tag.find_element(By.CSS_SELECTOR, "span.keyword-recommend-tag-text")
                    if span.text.strip() == keyword_name:
                        self.driver.execute_script("arguments[0].click();", tag)
                        time.sleep(0.2)
                        return True
                except:
                    continue
            return False
        except Exception:
            return False

    def _wait_for_element(self, selector: str, timeout: int = 10):
        """要素が表示されるまで待機"""
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            return element
        except TimeoutException:
            return None

    def step1_select_article_type(self, article_type: str):
        """ステップ1: 作品形式を選択"""
        self._report("ステップ1: 作品形式を選択中...")

        # article_type の値: comic, cg, game, voice
        if self._click_radio("article_type", article_type):
            time.sleep(1)  # UI更新待ち
            self._report(f"作品形式を選択しました: {article_type}")
            return True
        return False

    def step2_select_ai_type(self, ai_type: str):
        """ステップ2: AI利用の有無を選択"""
        self._report("ステップ2: AI利用の有無を選択中...")

        # ai_generated_type の値: 1, 2, 3, 4
        if self._click_radio("ai_generated_type", ai_type):
            time.sleep(2)  # 他のフィールドが表示されるまで待機
            self._report(f"AI利用の有無を選択しました: {ai_type}")
            return True
        return False

    def step3_fill_basic_info(self, data: dict):
        """ステップ3: 基本情報を入力"""
        self._report("ステップ3: 基本情報を入力中...")

        # タイトル
        if data.get("title"):
            self._fill_input("title", data["title"])
            self._report(f"タイトル入力完了")

        # ふりがな
        if data.get("title_ruby"):
            self._fill_input("title_ruby", data["title_ruby"])
            self._report(f"ふりがな入力完了")

        # 作品区分（男性向け等）
        if data.get("section"):
            if self._click_radio("section", data["section"]):
                self._report(f"作品区分選択完了: {data['section']}")

        # 年齢指定
        if data.get("keyword_age"):
            self._click_radio("keyword_age", data["keyword_age"])

        self._report("基本情報入力完了")
        return True

    def step4_fill_content_info(self, data: dict):
        """ステップ4: 作品内容を入力"""
        self._report("ステップ4: 作品内容を入力中...")

        # 作品説明（コメント）
        if data.get("comment"):
            self._fill_textarea("comment", data["comment"])
            self._report("説明文入力完了")

        # ページ数・枚数
        if data.get("file_number"):
            self._fill_input("file_number", data["file_number"])
            self._report(f"枚数入力完了: {data['file_number']}")

        self._report("作品内容入力完了")
        return True

    def step5_fill_parody_info(self, data: dict):
        """ステップ5: パロディ情報を入力"""
        self._report("ステップ5: パロディ情報を入力中...")

        # パロディタイプ
        if data.get("parody_type"):
            self._click_radio("parody_type", data["parody_type"])
            time.sleep(0.5)

        # パロディ詳細（作品名、キャラクター名）
        # HTML: <div class="parody-names"><input type="text" maxlength="64" value="">...</div>
        parody_names = data.get("parody_names", [])
        try:
            parody_inputs = self.driver.find_elements(
                By.CSS_SELECTOR, "div.parody-names input[type='text']"
            )
            for i, name in enumerate(parody_names):
                if name and i < len(parody_inputs):
                    parody_inputs[i].clear()
                    parody_inputs[i].send_keys(name)
                    time.sleep(0.2)
        except Exception as e:
            self._report(f"パロディ詳細入力エラー: {e}")

        self._report("パロディ情報入力完了")
        return True

    def step6_select_keywords(self, keywords: list):
        """ステップ6: キーワードを選択"""
        self._report("ステップ6: キーワードを選択中...")

        # 人気キーワードのID一覧を取得
        popular_keyword_ids = {str(k) for k, v in POPULAR_KEYWORDS}

        selected_count = 0
        for keyword_id in keywords[:10]:  # 最大10個
            keyword_id_str = str(keyword_id)
            keyword_name = get_keyword_name(keyword_id_str)

            if not keyword_name:
                self._report(f"  キーワード名が見つかりません: {keyword_id_str}")
                continue

            # 人気キーワードの場合はまず人気タグをクリック
            if keyword_id_str in popular_keyword_ids:
                if self._click_popular_keyword(keyword_name):
                    selected_count += 1
                    self._report(f"  人気キーワード選択: {keyword_name}")
                    continue

            # カテゴリ内キーワードの場合はキーワード名で検索
            # 全カテゴリタブを順番に検索してチェックボックスを選択
            if self._click_keyword_by_name(keyword_name):
                selected_count += 1
                self._report(f"  キーワード選択: {keyword_name}")
            else:
                self._report(f"  キーワードが見つかりません: {keyword_name}")

        self._report(f"キーワード選択完了: {selected_count}個")
        return True

    def step7_fill_sales_info(self, data: dict):
        """ステップ7: 販売情報を入力"""
        self._report("ステップ7: 販売情報を入力中...")

        # 販売価格
        if data.get("price_retail"):
            self._fill_input("price_retail", data["price_retail"])
            self._report(f"販売価格入力完了: {data['price_retail']}円")

        # 専売希望
        if data.get("monopoly_hope_flg"):
            self._click_radio("monopoly_hope_flg", data["monopoly_hope_flg"])

        # 作品保護
        if data.get("drm_hope"):
            self._click_radio("drm_hope", data["drm_hope"])

        # キャンペーン参加
        # campaign_kibou_flg と is_coupon_usable はチェックボックスの場合がある

        # キャンペーン自動参加
        if data.get("campaign_auto_join_flg_set_days"):
            self._click_radio("campaign_auto_join_flg_set_days", data["campaign_auto_join_flg_set_days"])

        # 割引設定
        discount_flg = data.get("pre_release_articles_campaign_flg", "1")
        if self._click_radio("pre_release_articles_campaign_flg", discount_flg):
            self._report(f"割引設定: {'設定する' if discount_flg == '1' else '設定しない'}")
            time.sleep(0.5)

        # 割引設定が有効な場合のみ詳細を設定
        if discount_flg == "1":
            # 実施期間
            discount_days = data.get("pre_release_articles_campaign_discount_days", "28")
            if self._select_dropdown("pre_release_articles_campaign_discount_days", discount_days):
                self._report(f"割引実施期間: {discount_days}日")

            # 割引率
            discount_rate = data.get("pre_release_articles_campaign_discount_rate", "80")
            if self._select_dropdown("pre_release_articles_campaign_discount_rate", discount_rate):
                self._report(f"割引率: {discount_rate}%")

        self._report("販売情報入力完了")
        return True

    def step8_fill_other_info(self, data: dict):
        """ステップ8: その他の情報を入力"""
        self._report("ステップ8: その他の情報を入力中...")

        # 配信開始日タイプ
        if data.get("release_date_type"):
            self._click_radio("release_date_type", data["release_date_type"])

        # 作品修正対応
        if data.get("revision_flg"):
            self._click_radio("revision_flg", data["revision_flg"])

        # 通信欄
        if data.get("note"):
            self._fill_textarea("note", data["note"])
            self._report("通信欄入力完了")

        self._report("その他の情報入力完了")
        return True

    def fill_form(self, data: dict):
        """フォームに一括入力（全ステップ実行）"""
        self._report("=== 自動入力開始 ===")

        try:
            # ステップ1: 作品形式
            if not self.step1_select_article_type(data.get("article_type", "cg")):
                return False

            # ステップ2: AI利用の有無
            if not self.step2_select_ai_type(data.get("ai_generated_type", "2")):
                return False

            # ステップ3: 基本情報
            self.step3_fill_basic_info(data)

            # ステップ4: 作品内容
            self.step4_fill_content_info(data)

            # ステップ5: パロディ情報
            self.step5_fill_parody_info(data)

            # ステップ6: キーワード
            self.step6_select_keywords(data.get("keywords", []))

            # ステップ7: 販売情報
            self.step7_fill_sales_info(data)

            # ステップ8: その他
            self.step8_fill_other_info(data)

            self._report("=== 自動入力完了 ===")
            return True

        except Exception as e:
            self._report(f"エラーが発生しました: {e}")
            return False


# テスト用
if __name__ == "__main__":
    print("このモジュールはmain.pyから使用してください")
