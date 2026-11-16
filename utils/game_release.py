"""
ゲーム出品用: リリースフォルダ（例: C:/AI_dev/麻雀game/release）から作品情報を読み取る。

フォルダ構成:
    <release>/FANZA-001/<作品ID>_<作品名>/
        商品登録用_<作品ID>.txt   … タイトル・シリーズ名（伏せ字）・キャラクター名
        ゲーム説明文.txt           … ■作品紹介〜■クレジット を説明文に使う
"""
import json
import re
from pathlib import Path

DEFAULT_RELEASE_DIR = r"C:\AI_dev\麻雀game\release"
_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "game_settings.json"

PRODUCT_TXT_GLOB = "商品登録用_*.txt"
DESCRIPTION_TXT = "ゲーム説明文.txt"

# 説明文の開始見出し / 説明文から除外する見出し（販売ページ用 短文）
DESC_START_HEADING = "■作品紹介"
DESC_EXCLUDE_HEADING = "■販売ページ用"


def load_release_dir() -> str:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("release_dir") or DEFAULT_RELEASE_DIR
    except Exception:
        return DEFAULT_RELEASE_DIR


def save_release_dir(path: str):
    data = {}
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    data["release_dir"] = path
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return path.read_text(encoding=enc).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def find_account_folder(release_dir: str, profile_name: str):
    """プロファイル名（FANZA-001 等）と番号が一致するフォルダを返す（なければNone）。"""
    root = Path(release_dir)
    if not root.is_dir():
        return None
    exact = root / profile_name
    if exact.is_dir():
        return exact
    m = re.findall(r"\d+", profile_name or "")
    if not m:
        return None
    num = int(m[-1])
    prefix = re.sub(r"[-_]?\d+$", "", profile_name)
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        dm = re.findall(r"\d+", d.name)
        if dm and int(dm[-1]) == num and re.sub(r"[-_]?\d+$", "", d.name) == prefix:
            return d
    return None


def list_products(account_folder) -> list[Path]:
    """アカウントフォルダ内の作品フォルダ（商品登録用テキストがあるもの）を返す。"""
    if not account_folder:
        return []
    folder = Path(account_folder)
    return sorted(
        d for d in folder.iterdir()
        if d.is_dir() and any(d.glob(PRODUCT_TXT_GLOB))
    )


def parse_product_txt(text: str) -> dict:
    """商品登録用テキストを解析する。"""
    info = {"title": "", "title_ruby": "", "series": "", "series_masked": "", "characters": []}
    keys = {
        "タイトル": "title",
        "タイトルのふりがな": "title_ruby",
        "シリーズ名": "series",
        "シリーズ名（伏せ字）": "series_masked",
    }
    lines = text.split("\n")
    for line in lines:
        m = re.match(r"^\s*([^：:]+?)\s*[：:]\s*(.*)$", line)
        if m and m.group(1) in keys and not info[keys[m.group(1)]]:
            info[keys[m.group(1)]] = m.group(2).strip()

    # キャラクター名（1行1名・伏せ字無し）の次行から空行まで
    for i, line in enumerate(lines):
        if line.strip().startswith("キャラクター名") and "伏せ字無し" in line:
            for name in lines[i + 1:]:
                if not name.strip():
                    break
                info["characters"].append(name.strip())
            break
    return info


def extract_description(text: str) -> str:
    """■作品紹介から最後まで（■販売ページ用 短文 のセクションは除外）を返す。"""
    sections = []  # [(見出し行, [行...])]
    for line in text.split("\n"):
        if line.startswith("■"):
            sections.append((line, [line]))
        elif sections:
            sections[-1][1].append(line)

    out = []
    started = False
    for heading, body in sections:
        if heading.startswith(DESC_START_HEADING):
            started = True
        if not started or heading.startswith(DESC_EXCLUDE_HEADING):
            continue
        block = "\n".join(body).strip("\n")
        out.append(block)
    return "\n\n".join(out).strip()


def build_title(title: str, series_masked: str) -> str:
    return f"{title} {series_masked}".strip() if series_masked else title


def load_game_product(product_folder) -> dict:
    """作品フォルダから出品に必要な情報をまとめて返す。

    戻り値: title / title_base / title_ruby / series / series_masked /
            characters / comment / errors（取得できなかった項目の説明）
    """
    folder = Path(product_folder)
    errors = []
    info = {"title_ruby": "", "series": "", "series_masked": "", "characters": [], "title": ""}

    txts = sorted(folder.glob(PRODUCT_TXT_GLOB))
    if txts:
        info = parse_product_txt(_read_text(txts[0]))
        if not info["title"]:
            errors.append("商品登録用テキストに「タイトル：」がありません")
        if not info["series_masked"]:
            errors.append("商品登録用テキストに「シリーズ名（伏せ字）：」がありません")
        if not info["characters"]:
            errors.append("商品登録用テキストにキャラクター名がありません")
    else:
        errors.append("商品登録用_*.txt が見つかりません")

    comment = ""
    desc_path = folder / DESCRIPTION_TXT
    if desc_path.exists():
        comment = extract_description(_read_text(desc_path))
        if not comment:
            errors.append("ゲーム説明文.txt に「■作品紹介」がありません")
    else:
        errors.append("ゲーム説明文.txt が見つかりません")

    return {
        "title": build_title(info["title"], info["series_masked"]),
        "title_base": info["title"],
        "title_ruby": info["title_ruby"],
        "series": info["series"],
        "series_masked": info["series_masked"],
        "characters": info["characters"],
        "comment": comment,
        "errors": errors,
    }
