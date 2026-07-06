"""
ふりがな変換モジュール
pykakasiを使用して日本語テキストをふりがなに変換
OpenAI APIはオプションとして使用可能
"""
import re
try:
    import pykakasi
    PYKAKASI_AVAILABLE = True
except ImportError:
    PYKAKASI_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class FuriganaConverter:
    """ふりがな変換クラス"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.client = None
        if api_key and OPENAI_AVAILABLE:
            self.client = OpenAI(api_key=api_key)

        # pykakasiの初期化
        self.kakasi = None
        if PYKAKASI_AVAILABLE:
            self.kakasi = pykakasi.kakasi()
            self.kakasi.setMode("H", "H")  # ひらがな→ひらがな
            self.kakasi.setMode("K", "H")  # カタカナ→ひらがな
            self.kakasi.setMode("J", "H")  # 漢字→ひらがな
            self.kakasi.setMode("r", "Hepburn")  # ローマ字変換方式
            self.converter = self.kakasi.getConverter()

    def set_api_key(self, api_key: str):
        """APIキーを設定"""
        self.api_key = api_key
        if api_key and OPENAI_AVAILABLE:
            self.client = OpenAI(api_key=api_key)
        else:
            self.client = None

    def convert_local(self, text: str) -> tuple[bool, str]:
        """
        pykakasiを使ってローカルでふりがな変換

        Args:
            text: 変換する日本語テキスト

        Returns:
            (成功フラグ, ふりがな or エラーメッセージ)
        """
        if not PYKAKASI_AVAILABLE:
            return False, "pykakasiがインストールされていません"

        if not text:
            return False, "テキストが空です"

        try:
            # pykakasiで変換
            result = self.converter.do(text)
            return True, result
        except Exception as e:
            return False, f"変換エラー: {e}"

    def convert_openai(self, text: str) -> tuple[bool, str]:
        """
        OpenAI APIを使ってふりがな変換

        Args:
            text: 変換する日本語テキスト

        Returns:
            (成功フラグ, ふりがな or エラーメッセージ)
        """
        if not self.client:
            return False, "OpenAI APIキーが設定されていません"

        if not text:
            return False, "テキストが空です"

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """あなたは日本語のふりがな変換の専門家です。
入力されたテキストをひらがなのふりがなに変換してください。

ルール:
1. 漢字はひらがなに変換する
2. カタカナはひらがなに変換する
3. 数字とアルファベットはそのまま残す
4. 記号や空白はそのまま残す
5. ふりがなのみを出力する（説明や余分な文字は不要）

例:
入力: 東京タワー123
出力: とうきょうたわー123

入力: ABC商事
出力: ABCしょうじ"""
                    },
                    {
                        "role": "user",
                        "content": f"以下のテキストをふりがなに変換してください:\n{text}"
                    }
                ],
                temperature=0,
                max_tokens=500,
            )

            furigana = response.choices[0].message.content.strip()
            return True, furigana

        except Exception as e:
            error_msg = str(e)
            if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
                return False, "APIキーが無効です"
            return False, f"変換エラー: {error_msg}"

    def convert(self, text: str, use_openai: bool = False) -> tuple[bool, str]:
        """
        テキストをふりがなに変換

        Args:
            text: 変換する日本語テキスト
            use_openai: TrueならOpenAI APIを優先使用

        Returns:
            (成功フラグ, ふりがな or エラーメッセージ)
        """
        if not text:
            return False, "テキストが空です"

        # OpenAI優先の場合
        if use_openai and self.client:
            success, result = self.convert_openai(text)
            if success:
                return True, result

        # ローカル変換（pykakasi）を使用
        if PYKAKASI_AVAILABLE:
            return self.convert_local(text)

        # pykakasiがない場合はOpenAIを試す
        if self.client:
            return self.convert_openai(text)

        return False, "変換手段がありません（pykakasiまたはOpenAI APIキーが必要）"


# テスト用
if __name__ == "__main__":
    converter = FuriganaConverter()
    test_texts = [
        "東京タワー",
        "ABC商事123",
        "魔法少女まどか☆マギカ",
        "先生の性処理オナホ化計画",
    ]
    for text in test_texts:
        success, result = converter.convert(text)
        print(f"{text} -> {result} ({'OK' if success else 'NG'})")
