"""
ふりがな変換モジュール
OpenAI APIを使用して日本語テキストをふりがなに変換
"""
from openai import OpenAI


class FuriganaConverter:
    """OpenAI APIを使ったふりがな変換"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        if api_key:
            self.client = OpenAI(api_key=api_key)

    def set_api_key(self, api_key: str):
        """APIキーを設定"""
        self.api_key = api_key
        if api_key:
            self.client = OpenAI(api_key=api_key)
        else:
            self.client = None

    def convert(self, text: str) -> tuple[bool, str]:
        """
        テキストをふりがなに変換

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


# テスト用
if __name__ == "__main__":
    import os

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        converter = FuriganaConverter(api_key)
        test_texts = [
            "東京タワー",
            "ABC商事123",
            "魔法少女まどか☆マギカ",
        ]
        for text in test_texts:
            success, result = converter.convert(text)
            print(f"{text} -> {result} ({'OK' if success else 'NG'})")
    else:
        print("OPENAI_API_KEY not set")
