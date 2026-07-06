"""
FANZA同人 フォーム自動入力モジュール
"""
import re
import time
import sys
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    UnexpectedAlertPresentException,
)

# パス設定
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.keywords_data import POPULAR_KEYWORDS, get_keyword_name, NAME_TO_ID


# ジャンル欄に並ぶが実キーワードではない・取得対象外にするリンク文言
EXCLUDED_KEYWORD_NAMES = {
    "男性向け",
    "成人向け",
    "新作",
    "準新作",
    "NEW",
    "AI",
    "（AI）",
    "(AI)",
    "NEW（AI）",
    "NEW(AI)",
    "サークル",
}


def extract_keywords_from_page(driver, callback=None, max_keywords: int = 10):
    """現在ブラウザで開いているページからジャンル（キーワード）を抽出してID一覧を返す。

    配信中の作品ページ（公開ページのジャンルリンク）や配信編集ページ
    （チェック済みキーワード）を開いた状態で呼び出すと、前回設定した
    キーワードを読み取ってキーワードIDのリストに変換する。

    Args:
        driver: Selenium WebDriver（AdsPowerで開いている作品ページ）
        callback: 進捗報告用コールバック
        max_keywords: 取得する最大件数

    Returns:
        list[str]: マッチしたキーワードIDのリスト（出現順・重複除去済み）
    """
    report = callback or (lambda m: print(m))

    matched_ids: list[str] = []
    seen: set[str] = set()
    debug_lines: list[str] = []  # 診断用

    def _add(name: str, source: str = "") -> bool:
        name = (name or "").strip()
        if not name:
            return False
        if name in EXCLUDED_KEYWORD_NAMES:
            debug_lines.append(f"[SKIP:{source}] {name}")
            return False
        kid = NAME_TO_ID.get(name)
        if kid and kid not in seen:
            seen.add(kid)
            matched_ids.append(kid)
            debug_lines.append(f"[MATCH:{source}] {name} -> {kid}")
            return True
        return False

    def _scan_current_page():
        """「ジャンル」ラベル横の青い文字（リンク <a>）だけからキーワードを取得。

        テキストやページ全体のスキャンは行わず、ジャンル欄のリンクのみを対象にする。
        """
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            debug_lines.append("[body]\n" + body.text)
        except Exception:
            pass

        try:
            # 「ジャンル」または「ジャンル：」というラベル要素を探す
            labels = driver.find_elements(
                By.XPATH,
                "//*[normalize-space(text())='ジャンル' or normalize-space(text())='ジャンル：']",
            )
        except Exception:
            labels = []

        for label in labels:
            # ラベルの祖先（行・ブロック）を辿り、その中のリンク <a> のみを対象にする
            containers = []
            try:
                containers.append(label.find_element(By.XPATH, "./.."))
            except Exception:
                pass
            try:
                containers.append(label.find_element(By.XPATH, "./../.."))
            except Exception:
                pass

            for container in containers:
                got = False
                try:
                    for a in container.find_elements(By.TAG_NAME, "a"):
                        try:
                            # ラベル自身がリンクの場合は除外
                            if a.text.strip() in ("ジャンル", "ジャンル："):
                                continue
                            if _add(a.text, "genre-a"):
                                got = True
                        except Exception:
                            continue
                except Exception:
                    pass
                if got:
                    return  # このタブのジャンル欄から取得できたので終了

    # 全タブ（ウィンドウハンドル）を走査する。
    # ユーザーが作品ページを新しいタブで開くと、Seleniumは別タブを
    # 見ていることがあるため、開いている全タブを順番に調べる。
    original_handle = None
    try:
        original_handle = driver.current_window_handle
    except Exception:
        original_handle = None

    handles = []
    try:
        handles = list(driver.window_handles)
    except Exception:
        handles = []

    if handles:
        for handle in handles:
            try:
                driver.switch_to.window(handle)
            except Exception:
                continue
            try:
                url = driver.current_url
            except Exception:
                url = "?"
            report(f"タブを確認中: {url}")
            debug_lines.append(f"\n===== TAB: {url} =====")
            _scan_current_page()
    else:
        # ウィンドウハンドルが取得できない場合は現在のページのみ
        _scan_current_page()

    # 元のタブに戻す
    if original_handle is not None:
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass

    result = matched_ids[:max_keywords]
    names = [get_keyword_name(k) for k in result]

    # 診断用にページ内容と一致結果をファイル出力（毎回）
    try:
        dump_path = Path(__file__).parent.parent / "debug_keywords.txt"
        header = "===== 一致したキーワード =====\n" + (
            "\n".join(f"{get_keyword_name(k)} ({k})" for k in result) or "(なし)"
        ) + "\n\n"
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(debug_lines))
    except Exception:
        pass

    report(
        f"ページからキーワードを{len(result)}件取得しました: "
        + (", ".join(names) if names else "(なし)")
    )
    return result


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
        """進捗を報告（診断用にログファイルへも追記）"""
        self.callback(message)
        try:
            log_path = Path(__file__).parent.parent / "debug_formal_log.txt"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(str(message) + "\n")
        except Exception:
            pass

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
            return self._fill_input_element(input_elem, value)
        except Exception as e:
            self._report(f"入力エラー ({name}): {e}")
            return False

    def _fill_input_element(self, input_elem, value: str):
        """入力要素に値を設定（要素を直接渡す版）"""
        try:
            # 要素をクリックしてフォーカス
            self.driver.execute_script("arguments[0].click();", input_elem)
            time.sleep(0.2)

            # まずsend_keysで直接入力を試す（最も確実な方法）
            input_elem.clear()
            time.sleep(0.1)
            input_elem.send_keys(value)
            time.sleep(0.2)

            # 値が入ったか確認
            current_value = input_elem.get_attribute("value")
            if current_value == value:
                return True

            # send_keysで入らなかった場合、JavaScript方式を試す
            self.driver.execute_script("""
                var input = arguments[0];
                var value = arguments[1];

                // React用: nativeInputValueSetterを使用
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(input, value);

                // 各種イベントを発火してフレームワークに変更を通知
                var inputEvent = new Event('input', { bubbles: true, cancelable: true });
                input.dispatchEvent(inputEvent);

                var changeEvent = new Event('change', { bubbles: true, cancelable: true });
                input.dispatchEvent(changeEvent);

                // フォーカスイベントも発火
                input.dispatchEvent(new Event('blur', { bubbles: true }));
                input.dispatchEvent(new Event('focus', { bubbles: true }));
            """, input_elem, value)
            time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"入力要素エラー: {e}")
            return False

    def _fill_textarea(self, name: str, value: str):
        """テキストエリアに値を設定"""
        try:
            textarea = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"textarea[name='{name}']"))
            )
            # 要素をクリックしてフォーカス
            self.driver.execute_script("arguments[0].click();", textarea)
            time.sleep(0.2)

            # React/Vueなどのフレームワーク対応
            # nativeInputValueSetterを使用して内部状態を直接更新
            self.driver.execute_script("""
                var textarea = arguments[0];
                var value = arguments[1];

                // React用: nativeInputValueSetterを使用
                var nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                nativeTextAreaValueSetter.call(textarea, value);

                // 各種イベントを発火してフレームワークに変更を通知
                var inputEvent = new Event('input', { bubbles: true, cancelable: true });
                textarea.dispatchEvent(inputEvent);

                var changeEvent = new Event('change', { bubbles: true, cancelable: true });
                textarea.dispatchEvent(changeEvent);

                // フォーカスイベントも発火
                textarea.dispatchEvent(new Event('blur', { bubbles: true }));
                textarea.dispatchEvent(new Event('focus', { bubbles: true }));
            """, textarea, value)
            time.sleep(0.5)
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
            time.sleep(1.5)  # ふりがなフィールドが有効になるまで待機（React再レンダリング待ち）

        # ふりがな
        if data.get("title_ruby"):
            furigana_filled = False

            # 方法1: 複数のフィールド名を試す
            for field_name in ["title_kana", "title_ruby", "title_furigana", "furigana", "kana"]:
                try:
                    input_elem = self.driver.find_element(By.CSS_SELECTOR, f"input[name='{field_name}']")
                    if input_elem and input_elem.is_enabled():
                        self._fill_input_element(input_elem, data["title_ruby"])
                        furigana_filled = True
                        self._report(f"ふりがな入力完了 (field: {field_name})")
                        break
                except:
                    continue

            # 方法2: タイトルフィールドの次の入力フィールドを探す
            if not furigana_filled:
                try:
                    title_input = self.driver.find_element(By.CSS_SELECTOR, "input[name='title']")
                    # 親要素から次の入力フィールドを探す
                    parent = title_input.find_element(By.XPATH, "./..")
                    next_inputs = parent.find_elements(By.XPATH, "following::input[@type='text']")
                    if next_inputs:
                        for next_input in next_inputs[:3]:  # 最初の3つの入力フィールドを試す
                            if next_input.is_enabled() and next_input.get_attribute("name") != "title":
                                self._fill_input_element(next_input, data["title_ruby"])
                                furigana_filled = True
                                self._report(f"ふりがな入力完了 (次の入力フィールド)")
                                break
                except Exception as e:
                    self._report(f"次の入力フィールド検索エラー: {e}")

            # 方法3: プレースホルダーやラベルでふりがなフィールドを探す
            if not furigana_filled:
                try:
                    selectors = [
                        "input[placeholder*='ふりがな']",
                        "input[placeholder*='フリガナ']",
                        "input[placeholder*='ひらがな']",
                        "input[placeholder*='かな']",
                        "input[placeholder*='カナ']",
                    ]
                    for selector in selectors:
                        try:
                            input_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                            if input_elem and input_elem.is_enabled():
                                self._fill_input_element(input_elem, data["title_ruby"])
                                furigana_filled = True
                                self._report(f"ふりがな入力完了 (placeholder検索)")
                                break
                        except:
                            continue
                except:
                    pass

            # 方法4: ラベルテキストでふりがなフィールドを探す
            if not furigana_filled:
                try:
                    labels = self.driver.find_elements(By.TAG_NAME, "label")
                    for label in labels:
                        label_text = label.text.strip()
                        if "ふりがな" in label_text or "フリガナ" in label_text or "読み" in label_text:
                            for_attr = label.get_attribute("for")
                            if for_attr:
                                input_elem = self.driver.find_element(By.ID, for_attr)
                            else:
                                # ラベルの次の入力フィールドを探す
                                input_elem = label.find_element(By.XPATH, "following::input[1]")
                            if input_elem and input_elem.is_enabled():
                                self._fill_input_element(input_elem, data["title_ruby"])
                                furigana_filled = True
                                self._report(f"ふりがな入力完了 (ラベル検索)")
                                break
                except Exception as e:
                    self._report(f"ラベル検索エラー: {e}")

            if not furigana_filled:
                self._report("ふりがなフィールドが見つかりませんでした")

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


class FanzaTrailerFormFiller(FanzaFormFiller):
    """FANZA同人 予約作品（予告）フォーム自動入力クラス"""

    FORM_URL = "https://dojin.dmm.co.jp/addtrailer"

    def navigate_to_form(self):
        """予約作品登録フォームページに移動"""
        self._report("予約作品フォームページを開いています...")
        self.driver.get(self.FORM_URL)
        time.sleep(3)
        self._report("予約作品フォームページを開きました")

    def step7_fill_sales_info(self, data: dict):
        """ステップ7: 販売情報を入力（予約作品用）"""
        self._report("ステップ7: 販売情報を入力中（予約作品）...")

        # 販売価格未定チェック
        # サイトのデフォルトは「未定」にチェックが入っており、価格を入力できない状態。
        # price_undecided が False の場合は未定チェックを外してから設定価格を入力する。
        price_undecided = data.get("price_undecided", False)
        # 未定チェックボックスを探す（price_undecided または price_retail_undecided）
        price_checkbox = None
        try:
            price_checkbox = self.driver.find_element(
                By.CSS_SELECTOR, "input[name='price_undecided'], input[name='price_retail_undecided'], input[type='checkbox'][id*='price']"
            )
        except Exception:
            price_checkbox = None

        if price_undecided:
            # 未定チェックボックスを選択
            if price_checkbox is not None:
                if not price_checkbox.is_selected():
                    self.driver.execute_script("arguments[0].click();", price_checkbox)
                    time.sleep(0.3)
                self._report("販売価格: 未定")
            elif self._click_radio("price_undecided", "1"):
                self._report("販売価格: 未定")
            else:
                self._report("価格未定チェックボックスが見つかりません")
        else:
            # 未定チェックを外してから価格を入力
            if price_checkbox is not None and price_checkbox.is_selected():
                self.driver.execute_script("arguments[0].click();", price_checkbox)
                time.sleep(0.3)
                self._report("販売価格: 未定チェックを解除")
            # 価格を入力
            if data.get("price_retail"):
                self._fill_input("price_retail", data["price_retail"])
                self._report(f"販売価格入力完了: {data['price_retail']}円")

        # 専売希望
        if data.get("monopoly_hope_flg"):
            self._click_radio("monopoly_hope_flg", data["monopoly_hope_flg"])

        # 作品保護
        if data.get("drm_hope"):
            self._click_radio("drm_hope", data["drm_hope"])

        self._report("販売情報入力完了（予約作品）")
        return True

    def step8_fill_other_info(self, data: dict):
        """ステップ8: その他の情報を入力（予約作品用）"""
        self._report("ステップ8: その他の情報を入力中（予約作品）...")

        # 予告開始日指定
        trailer_release_date_type = data.get("trailer_release_date_type", "1")
        if self._click_radio("trailer_release_date_type", trailer_release_date_type):
            self._report(f"予告開始日: {'最短で公開' if trailer_release_date_type == '1' else '日付を指定して公開'}")

        # 配信予定未定チェック
        release_undecided = data.get("release_undecided", True)
        if release_undecided:
            try:
                # 未定チェックボックスを探す
                checkbox = self.driver.find_element(
                    By.CSS_SELECTOR, "input[name='release_undecided'], input[name='release_season_undecided'], input[type='checkbox'][id*='release']"
                )
                if not checkbox.is_selected():
                    self.driver.execute_script("arguments[0].click();", checkbox)
                    time.sleep(0.3)
                self._report("配信予定: 未定")
            except Exception as e:
                # ラジオボタンの場合
                if self._click_radio("release_undecided", "1"):
                    self._report("配信予定: 未定")
                else:
                    self._report(f"配信予定未定設定エラー: {e}")

        # 作品修正対応
        if data.get("revision_flg"):
            self._click_radio("revision_flg", data["revision_flg"])

        # 通信欄
        if data.get("note"):
            self._fill_textarea("note", data["note"])
            self._report("通信欄入力完了")

        self._report("その他の情報入力完了（予約作品）")
        return True

    def fill_form(self, data: dict):
        """予約作品フォームに一括入力"""
        self._report("=== 予約作品自動入力開始 ===")

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

            # ステップ7: 販売情報（予約作品用）
            self.step7_fill_sales_info(data)

            # ステップ8: その他（予約作品用）
            self.step8_fill_other_info(data)

            self._report("=== 予約作品自動入力完了 ===")
            return True

        except Exception as e:
            self._report(f"エラーが発生しました: {e}")
            return False


class FanzaFormalRegistrationFiller(FanzaFormFiller):
    """FANZA同人 配信申請（本登録 formal_registration）ページ入力クラス。

    予告作品を実際に出品するための配信申請フォームに対して、
    キャンペーン自動参加・割引設定を自動入力する。

    伏字タイトルを渡すと、作品管理ページ（作品・シリーズ一覧）から該当作品の
    配信申請リンク（formal_registration/input/{作品ID}）を探して自動で開いてから
    入力する。タイトルを渡さない場合は、すでに開いている配信申請ページに入力する。
    """

    CATALOG_URL = "https://dojin.dmm.co.jp/screening/catalog"

    def _origin(self):
        """現在のURLからオリジン（scheme://host）を取得。取得できなければ既定値。"""
        try:
            url = self.driver.current_url or ""
            m = re.match(r"^(https?://[^/]+)", url)
            if m:
                return m.group(1)
        except Exception:
            pass
        return "https://dojin.dmm.co.jp"

    # 丸数字（①～⑳）→ 半角数字への対応表
    _CIRCLED_NUMBERS = {
        "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
        "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9", "⑩": "10",
        "⑪": "11", "⑫": "12", "⑬": "13", "⑭": "14", "⑮": "15",
        "⑯": "16", "⑰": "17", "⑱": "18", "⑲": "19", "⑳": "20",
    }

    # 波ダッシュ／チルダ系のそっくり文字を統一する（全角チルダ～U+FF5E と
    # 波ダッシュ〜U+301C 等が混在するため）
    _WAVE_CHARS = {
        "〜": "~",  # 〜 WAVE DASH
        "～": "~",  # ～ FULLWIDTH TILDE
        "〰": "~",  # 〰 WAVY DASH
        "∼": "~",  # ∼ TILDE OPERATOR
        "⁓": "~",  # ⁓ SWUNG DASH
    }

    @classmethod
    def _normalize_title(cls, text: str) -> str:
        """タイトル比較用に正規化する。

        番号違い（①②/1 2 等）を正しく区別しつつ表記揺れを吸収するため、
        丸数字→半角数字・全角英数字→半角へ変換し、波ダッシュ/チルダ系を統一し、
        空白をまとめる。
        """
        if not text:
            return ""
        text = text.replace("　", " ")
        for c, n in cls._CIRCLED_NUMBERS.items():
            text = text.replace(c, n)
        # 波ダッシュ／チルダ系を "~" に統一
        for c, n in cls._WAVE_CHARS.items():
            text = text.replace(c, n)
        # 全角英数字・記号を半角へ（！-～ の範囲を 0x20 シフト）
        text = "".join(
            chr(ord(ch) - 0xFEE0) if "！" <= ch <= "～" else ch
            for ch in text
        )
        return " ".join(text.split()).strip()

    def _accept_any_alert(self):
        """未処理のダイアログ（離脱確認など）があれば承諾して閉じる"""
        try:
            self.driver.switch_to.alert.accept()
        except Exception:
            pass

    def _ensure_window(self):
        """現在のウィンドウが無効（前回操作で閉じた等）なら有効なタブに切り替える"""
        try:
            _ = self.driver.current_url
            return
        except Exception:
            pass
        try:
            handles = self.driver.window_handles
            if handles:
                self.driver.switch_to.window(handles[0])
        except Exception:
            pass

    def _safe_get(self, url: str):
        """ページ離脱確認ダイアログ（前回入力したフォーム等）を承諾しつつ遷移する"""
        self._ensure_window()
        self._accept_any_alert()
        try:
            self.driver.get(url)
        except UnexpectedAlertPresentException:
            self._accept_any_alert()
            try:
                self.driver.get(url)
            except Exception as e:
                self._report(f"ページ遷移エラー: {e}")
        except Exception as e:
            # ウィンドウ不正など: 有効タブに切り替えて再試行
            self._ensure_window()
            try:
                self.driver.get(url)
            except Exception as e2:
                self._report(f"ページ遷移エラー: {e2}")
        self._accept_any_alert()

    @staticmethod
    def _trailing_number(text: str) -> str:
        """タイトル末尾側の巻数などの数字を取り出す（無ければ空文字）。"""
        nums = re.findall(r"\d+", text or "")
        return nums[-1] if nums else ""

    def _titles_match(self, target: str, candidate: str) -> bool:
        """正規化済みタイトル同士の一致判定。

        巻数の表記差（「ユーフォニアム②」＝スペース無し vs 登録側
        「ユーフォニアム 2」＝スペース有り）を吸収するため、比較時は空白を
        全除去する。ただし番号（巻数）が異なる場合は取り違え防止のため必ず
        不一致にし、番号が同じ（または両方に番号が無い）場合はシリーズ名の
        表記揺れを吸収するため完全一致に加えて包含関係も一致とみなす。
        """
        if not target or not candidate:
            return False
        # 巻数の数字自体は保持したまま、空白のみを除去して比較する
        ct = re.sub(r"\s+", "", target)
        cc = re.sub(r"\s+", "", candidate)
        if not ct or not cc:
            return False
        if ct == cc:
            return True
        if self._trailing_number(ct) != self._trailing_number(cc):
            return False
        return ct in cc or cc in ct

    def _titles_for_article_id(self, article_id: str):
        """作品IDに紐づくタイトル候補（画像alt・詳細リンク文言）を集める。

        配信申請リンクの作品IDと、商品画像 img[src*='d_{ID}'] / 詳細リンク
        a[href*='cid=d_{ID}'] を突き合わせるため、表の構造に依存しない。
        """
        titles = []
        try:
            for img in self.driver.find_elements(
                By.CSS_SELECTOR, f"img[src*='d_{article_id}']"
            ):
                alt = (img.get_attribute("alt") or "").strip()
                if alt and alt != "カレンダー":
                    titles.append(alt)
        except Exception:
            pass
        try:
            for a in self.driver.find_elements(
                By.CSS_SELECTOR, f"a[href*='cid=d_{article_id}']"
            ):
                if a.text:
                    titles.append(a.text)
        except Exception:
            pass
        return titles

    def _all_titles_on_page(self):
        """現在のページ上の全作品タイトル（正規化済み・重複除去）を集める。"""
        seen = []
        try:
            imgs = self.driver.find_elements(By.CSS_SELECTOR, "img[alt]")
        except Exception:
            imgs = []
        for img in imgs:
            alt = (img.get_attribute("alt") or "").strip()
            if alt and alt != "カレンダー":
                nt = self._normalize_title(alt)
                if nt and nt not in seen:
                    seen.append(nt)
        try:
            dets = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/detail/']")
        except Exception:
            dets = []
        for a in dets:
            t = (a.text or "").strip()
            if t:
                nt = self._normalize_title(t)
                if nt and nt not in seen:
                    seen.append(nt)
        return seen

    def _formal_link_row(self, link):
        """配信申請リンクが属する作品行（tbody直下のtr）要素を返す。無ければNone。"""
        for xp in (
            # 作品管理表の tbody 直下の tr（内部ネスト表の tr は除外＝1作品単位）
            "./ancestor::tr[parent::tbody/parent::table"
            "[contains(@class,'workmanage-table')]][1]",
            "./ancestor::tr[parent::tbody][1]",
        ):
            try:
                row = link.find_element(By.XPATH, xp)
                if row is not None:
                    return row
            except Exception:
                continue
        return None

    def _titles_in_element(self, elem):
        """要素内の作品タイトル候補（画像alt・詳細リンク文言）を集める。"""
        titles = []
        try:
            for img in elem.find_elements(By.TAG_NAME, "img"):
                alt = (img.get_attribute("alt") or "").strip()
                if alt and alt != "カレンダー":
                    titles.append(alt)
        except Exception:
            pass
        try:
            for a in elem.find_elements(By.CSS_SELECTOR, "a[href*='/detail/']"):
                if a.text:
                    titles.append(a.text)
        except Exception:
            pass
        return titles

    def _find_button_by_title(self, target: str):
        """一致する作品の配信申請ボタン(<a>)を返す。

        各配信申請リンクについて、同じ作品行内のタイトル（画像alt・詳細リンク）と、
        作品IDに紐づくタイトルの両方を照合する。予告作品は公開画像/詳細リンクが
        まだ無いことがあるため、行内タイトルを主、作品IDを従とする。
        """
        if not target:
            return None
        try:
            links = self.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='/formal_registration/input/']"
            )
        except Exception:
            links = []

        for link in links:
            href = link.get_attribute("href") or ""
            titles = []

            # 主: 同じ作品行内のタイトル
            row = self._formal_link_row(link)
            if row is not None:
                titles.extend(self._titles_in_element(row))

            # 従: 作品IDに紐づくタイトル（公開後の画像/詳細リンク）
            m = re.search(r"/formal_registration/input/(\d+)", href)
            if m:
                titles.extend(self._titles_for_article_id(m.group(1)))

            for t in titles:
                if self._titles_match(target, self._normalize_title(t)):
                    return link
        return None

    def _matching_row_exists(self, target: str):
        """一致タイトルの作品行が現在ページに存在するか（配信申請ボタンの有無は問わない）。

        出品済み（ボタンが無い）判定に使う。全画像altを無差別に見るのではなく、
        作品管理表の作品行単位で判定して誤検出を避ける。
        """
        if not target:
            return False
        for row in self._iter_work_rows():
            for t in self._titles_in_element(row):
                if self._titles_match(target, self._normalize_title(t)):
                    return True
        return False

    def _iter_work_rows(self):
        """作品管理表の作品行（tbody直下のtr）を返す。class差異に備え複数手段。"""
        for sel in (
            "table.workmanage-table > tbody > tr",
            "table[class*='workmanage'] > tbody > tr",
        ):
            try:
                rows = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                rows = []
            if rows:
                return rows
        return []

    def _click_formal_link(self, link, href: str = ""):
        """配信申請ボタン(<a>)をクリックして配信申請ページへ遷移する。

        まずボタンを実クリックし、遷移しなければhrefへ直接遷移する。
        """
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", link
            )
            time.sleep(0.3)
            self.driver.execute_script("arguments[0].click();", link)
            time.sleep(3)
            cur = self.driver.current_url or ""
            self._report(f"クリック後のURL: {cur}")
            if "formal_registration" in cur:
                return True
        except Exception as e:
            self._report(f"配信申請ボタンのクリックに失敗しました: {e}")

        # フォールバック: href へ直接遷移
        if href:
            if not href.startswith("http"):
                href = self._origin() + href
            self._report(f"URL直接遷移を試みます: {href}")
            try:
                self._safe_get(href)
                time.sleep(3)
                cur = self.driver.current_url or ""
                self._report(f"遷移後のURL: {cur}")
                return "formal_registration" in cur
            except Exception as e:
                self._report(f"配信申請ページへの遷移に失敗しました: {e}")
        return False

    def open_formal_registration_by_title(self, masked_title: str, masked_product: str = "", max_pages: int = 20):
        """作品管理ページから、選択作品の配信申請ボタンを完全一致で探してクリックし開く。

        Returns:
            "opened"    : 配信申請ページを開けた
            "published" : 作品は見つかったが配信申請ボタンが無い（すでに出品済み）
            "notfound"  : 作品が見つからなかった
        """
        target = self._normalize_title(masked_title)

        # 実行ごとに診断ログを初期化
        try:
            log_path = Path(__file__).parent.parent / "debug_formal_log.txt"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("=== 配信申請 自動オープン ログ ===\n")
        except Exception:
            pass

        self._report(f"作品管理ページから配信申請ボタンを探しています: {masked_title}")

        # 前回入力したフォームからの離脱確認ダイアログを承諾しつつ遷移する
        self._safe_get(self.CATALOG_URL)

        # 作品一覧が描画されるまで待つ（配信申請リンク or 商品画像 のいずれか）
        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: d.find_elements(
                    By.CSS_SELECTOR, "a[href*='/formal_registration/input/']"
                ) or d.find_elements(By.CSS_SELECTOR, "a[href*='/detail/']")
            )
        except TimeoutException:
            self._report(
                "作品管理ページの作品一覧が表示されません。"
                "ログイン状態・表示カテゴリ（男性向け/TL/BL）をご確認ください。"
            )
        time.sleep(1.5)

        # 診断用に作品管理ページを常に保存（不一致・誤判定時の構造確認用）
        self._dump_page("debug_catalog.html")

        self._report(f"照合ターゲット(正規化): {target}")

        seen_title = False
        last_titles: list[str] = []
        for page in range(1, max_pages + 1):
            # 診断: このページの配信申請リンク件数
            try:
                _fl = self.driver.find_elements(
                    By.CSS_SELECTOR, "a[href*='/formal_registration/input/']"
                )
                self._report(
                    f"[{page}ページ目] 配信申請ボタン {len(_fl)}件: "
                    + ", ".join((l.get_attribute("href") or "") for l in _fl[:10])
                )
            except Exception:
                pass

            # 1) 一致作品の配信申請ボタンを探す（行内タイトル＋作品IDで照合）→ 開く
            link = self._find_button_by_title(target)
            if link is not None:
                href = link.get_attribute("href") or ""
                self._report(f"配信申請ボタンをクリックします: {href}")
                if self._click_formal_link(link, href):
                    self._report("配信申請ページを開きました")
                    return "opened"
                self._report("配信申請ページを開けませんでした（クリック/遷移失敗）")
                return "notfound"

            # 2) ボタンは無いが、一致タイトルの作品行が存在するか（＝出品済み判定）
            #    無差別なタイトル収集ではなく作品行単位で判定して誤検出を避ける
            if self._matching_row_exists(target):
                seen_title = True

            page_titles = self._all_titles_on_page()
            if page_titles:
                last_titles = page_titles

            # 次ページへ（ページャの「次」リンクを辿る）
            if not self._go_to_next_catalog_page():
                break
            self._report(f"次のページを確認中...（{page + 1}ページ目）")
            time.sleep(1.5)

        # 配信申請ボタンは無いが、一致作品行は存在した → すでに出品済み
        if seen_title:
            self._report("この作品はすでに出品済みです（配信申請ボタンがありません）")
            return "published"

        # 診断用に作品管理ページを保存し、見つかったタイトルを報告
        self._dump_page("debug_catalog.html")
        shown = "、".join(last_titles[:30]) if last_titles else "(なし)"
        self._report(
            "該当作品が作品管理ページで見つかりませんでした。\n"
            f"探したタイトル: {target}\n"
            f"ページ上の作品: {shown}"
        )
        return "notfound"

    def _go_to_next_catalog_page(self):
        """作品管理ページのページャで次ページへ遷移。次が無ければFalse。"""
        candidates = [
            "//a[normalize-space(text())='次へ']",
            "//a[normalize-space(text())='次']",
            "//a[contains(@class,'pager') and (contains(., '次') or contains(., 'Next'))]",
            "//a[@rel='next']",
        ]
        for xp in candidates:
            try:
                el = self.driver.find_element(By.XPATH, xp)
            except Exception:
                el = None
            if el is not None:
                try:
                    self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(1.5)
                    return True
                except Exception:
                    continue
        return False

    def _switch_to_formal_registration_tab(self):
        """開いているタブの中から配信申請ページ(formal_registration)を探して切り替える"""
        try:
            handles = list(self.driver.window_handles)
        except Exception:
            handles = []

        for handle in handles:
            try:
                self.driver.switch_to.window(handle)
                url = self.driver.current_url or ""
            except Exception:
                continue
            if "formal_registration" in url:
                self._report(f"配信申請ページを検出しました: {url}")
                return True

        self._report("配信申請ページのタブが見つかりません。現在のページで入力を試みます。")
        return False

    def _dump_page(self, filename: str = "debug_formal_registration.html"):
        """デバッグ用に現在のページHTMLを保存（セレクタ調整用）"""
        try:
            html = self.driver.page_source
            path = Path(__file__).parent.parent / filename
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass

    def _click_input_by_label(self, label_texts: list):
        """ラベル文字列にlabel_textsのいずれかを含むチェックボックス/ラジオを選択する"""
        for lt in label_texts:
            try:
                labels = self.driver.find_elements(
                    By.XPATH, f"//label[contains(normalize-space(.), '{lt}')]"
                )
            except Exception:
                labels = []

            for label in labels:
                inp = None
                # label の for 属性から input を取得
                for_id = label.get_attribute("for")
                if for_id:
                    try:
                        inp = self.driver.find_element(By.ID, for_id)
                    except Exception:
                        inp = None
                # label 内の input
                if inp is None:
                    try:
                        inp = label.find_element(By.XPATH, ".//input")
                    except Exception:
                        inp = None
                # label 直前の input
                if inp is None:
                    try:
                        inp = label.find_element(By.XPATH, "./preceding::input[1]")
                    except Exception:
                        inp = None

                if inp is not None:
                    try:
                        if not inp.is_selected():
                            self.driver.execute_script("arguments[0].click();", inp)
                            time.sleep(0.3)
                        return True
                    except Exception:
                        continue
        return False

    def _find_select_by_label(self, label_texts: list):
        """ラベル文字列(いずれか)に近い<select>要素を返す"""
        for lt in label_texts:
            try:
                labels = self.driver.find_elements(
                    By.XPATH, f"//*[contains(normalize-space(text()), '{lt}')]"
                )
            except Exception:
                labels = []

            for label in labels:
                for xp in (
                    "./following::select[1]",
                    ".//select",
                    "./..//select",
                    "./../..//select",
                ):
                    try:
                        el = label.find_element(By.XPATH, xp)
                        if el is not None:
                            return el
                    except Exception:
                        continue
        return None

    def _select_option(self, select_elem, value=None, text_contains=None, fallback_last=False):
        """<select>要素で値・テキスト・最終オプションのいずれかで選択する"""
        try:
            options = select_elem.find_elements(By.TAG_NAME, "option")
            target = None

            if value is not None:
                for op in options:
                    if (op.get_attribute("value") or "").strip() == str(value):
                        target = op
                        break

            if target is None and text_contains is not None:
                for op in options:
                    if str(text_contains) in (op.text or ""):
                        target = op
                        break

            if target is None and fallback_last and options:
                target = options[-1]

            if target is None:
                return False

            self.driver.execute_script("arguments[0].selected = true;", target)
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                select_elem,
            )
            time.sleep(0.3)
            return True
        except Exception as e:
            self._report(f"オプション選択エラー: {e}")
            return False

    def fill_campaign_discount(self, data: dict):
        """配信申請ページのキャンペーン自動参加・割引設定を入力する。

        data:
            campaign_auto_join (bool): 販売からすぐに自動参加する
            discount_enabled (bool):   割引設定を設定する
            discount_days (str):       割引実施期間（例: "28"、一番下=最長）
            discount_rate (str):       割引率（例: "80"）
            file_number (str):         枚数（伏字ZIPから再取得した画像点数）
            release_date_type (str):   配信開始日指定（"1"=最短で公開 / "2"=日付指定）
            masked_title (str):        伏字タイトル。指定時は作品管理ページから
                                       該当作品の配信申請ページを自動で開く。

        Returns:
            "done"      : 入力まで完了
            "published" : すでに出品済み（配信申請ボタンが無く入力せず終了）
            "notfound"  : 配信申請ページが見つからず入力できなかった
        """
        self._report("=== 配信申請: キャンペーン・割引入力開始 ===")

        # 伏字タイトルが渡されていれば、作品管理ページから配信申請ページを自動で開く。
        masked_title = str(data.get("masked_title", "") or "").strip()
        masked_product = str(data.get("masked_product", "") or "").strip()
        if masked_title:
            status = self.open_formal_registration_by_title(masked_title, masked_product)
            if status == "published":
                self._report("=== すでに出品済みのため入力を行いません ===")
                return "published"
            if status != "opened":
                # 見つからない場合は、すでに開いている配信申請ページがあれば使う
                if not self._switch_to_formal_registration_tab():
                    self._report("=== 配信申請ページが見つからず入力を中止しました ===")
                    return "notfound"
        else:
            # タイトル未指定: すでに開いている配信申請ページに入力する
            self._switch_to_formal_registration_tab()

        self._dump_page()

        # 0. 枚数（file_number）を再取得値で更新して自動入力
        file_number = str(data.get("file_number", "") or "").strip()
        if file_number:
            if self._fill_input("file_number", file_number):
                self._report(f"枚数を入力しました: {file_number}枚")
            else:
                self._report("枚数の入力欄が見つかりませんでした（要確認）")

        # 1. キャンペーン自動参加: 販売からすぐに自動参加する
        if data.get("campaign_auto_join", True):
            # addproduct と同じフィールド名を優先（value "0" = すぐに自動参加）
            done = self._click_radio("campaign_auto_join_flg_set_days", "0")
            if not done:
                done = self._click_input_by_label(
                    ["販売からすぐに自動参加", "発売からすぐに自動参加", "すぐに自動参加"]
                )
            self._report(
                "キャンペーン自動参加: 販売からすぐに自動参加する"
                + ("" if done else "（要確認: 該当項目が見つかりません）")
            )
            time.sleep(0.3)

        # 2. 割引設定を設定する
        if data.get("discount_enabled", True):
            done = self._click_radio("pre_release_articles_campaign_flg", "1")
            if not done:
                done = self._click_input_by_label(["割引設定を設定する", "設定する"])
            self._report(
                "割引設定: 設定する"
                + ("" if done else "（要確認: 該当項目が見つかりません）")
            )
            time.sleep(0.5)

            # 3. 実施期間（一番下 = 最長の日数、既定28日）
            days = str(data.get("discount_days", "28"))
            done = self._select_dropdown(
                "pre_release_articles_campaign_discount_days", days
            )
            if not done:
                sel = self._find_select_by_label(["実施期間"])
                if sel is not None:
                    done = self._select_option(sel, value=days, fallback_last=True)
            self._report(
                f"割引実施期間: {days}日"
                + ("" if done else "（要確認: プルダウンが見つかりません）")
            )

            # 4. 割引率（既定80%）
            rate = str(data.get("discount_rate", "80"))
            done = self._select_dropdown(
                "pre_release_articles_campaign_discount_rate", rate
            )
            if not done:
                sel = self._find_select_by_label(["割引率"])
                if sel is not None:
                    done = self._select_option(sel, value=rate, text_contains=rate)
            self._report(
                f"割引率: {rate}%"
                + ("" if done else "（要確認: プルダウンが見つかりません）")
            )

        # 5. 配信開始日指定（割引設定の下）: 最短で公開にチェック
        release_date_type = str(data.get("release_date_type", "1") or "1")
        done = self._click_radio("release_date_type", release_date_type)
        if not done:
            labels = ["最短で公開"] if release_date_type == "1" else ["日付を指定して公開"]
            done = self._click_input_by_label(labels)
        self._report(
            ("配信開始日指定: 最短で公開" if release_date_type == "1" else "配信開始日指定: 日付を指定して公開")
            + ("" if done else "（要確認: 該当項目が見つかりません）")
        )

        self._report("=== 配信申請: 入力完了（内容を確認して手動で申請してください） ===")
        return "done"


# テスト用
if __name__ == "__main__":
    print("このモジュールはmain.pyから使用してください")
