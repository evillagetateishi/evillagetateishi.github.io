#!/usr/bin/env python3
"""data/rooms.json の検査。

やること:
  1. スキーマ検証        …… 必要な項目が、想定した型・値で入っているか
  2. 禁止パターン検査    …… 公開してはいけない値が紛れ込んでいないか

このリポジトリは公開されている。rooms.json に入れてよいのは、
Airbnb の公開ページや現地掲示と同じ「誰でも見られる情報」だけ。
暗証番号・PIN・ゲスト情報・認証情報・内部メモは入れない（docs/DATA_GUIDE.md）。

使い方:
    python scripts/validate.py
終了コード 0 = 合格 / 1 = 不合格
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOMS_JSON = os.path.join(ROOT, "data", "rooms.json")

ROOM_CODE_RE = re.compile(r"^EVT\d{3}$")
PHOTO_RE = re.compile(r"^images/rooms/evt\d{3}\.(png|jpg|webp)$")
AIRBNB_RE = re.compile(r"^https://www\.airbnb\.jp/rooms/\d+$")
# 許可番号は「7葛保生環令17号」の形。数字の桁数が増える将来も見越して 1〜4 桁を許す。
LICENSE_RE = re.compile(r"^\d{1,2}葛保生環令\d{1,4}号$")

# 値の中に現れてはいけない語。キー名・値の両方を小文字化して照合する。
FORBIDDEN_WORDS = [
    "password", "passwd", "passcode", "pincode", "pin code",
    "secret", "token", "api_key", "apikey", "access_key", "private_key",
    "credential", "smartlock", "keybox", "lockbox",
    "暗証", "パスワード", "解錠", "鍵番号", "キーボックス",
    "社内", "内部メモ", "非公開", "ゲスト名", "宿泊者名", "予約番号",
]

# 4桁以上の数字列は原則として疑う（暗証番号・キーボックス番号の混入を防ぐため）。
DIGIT_RUN_RE = re.compile(r"\d{4,}")

# ただし次の場所に現れる数字列は正当なので通す。
#   airbnb_url   … Airbnb の物件ID（19桁）
#   license.number … 許可番号（葛飾区保健所）
#   desc         … 「12th-century」のような西暦・世紀の表記
DIGIT_ALLOWED_PATHS = {
    "airbnb_url",
    "license.number",
    "photo.file",
}
# desc / name / amenities のような自由文では、年号（1000〜2999）だけ通す。
YEARLIKE_RE = re.compile(r"^(1[0-9]{3}|2[0-9]{3})$")

errors = []
warnings = []


def err(path, msg):
    errors.append("%s: %s" % (path, msg))


def check_type(path, value, types, label):
    if not isinstance(value, types):
        err(path, "%s であるべき（実際: %s）" % (label, type(value).__name__))
        return False
    return True


def walk_strings(node, path=""):
    """JSON を歩いて (パス, 文字列) を全部返す。キー名も検査対象にする。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (path, key) if path else key
            yield child, str(key)
            for item in walk_strings(value, child):
                yield item
    elif isinstance(node, list):
        for index, value in enumerate(node):
            for item in walk_strings(value, "%s[%d]" % (path, index)):
                yield item
    elif isinstance(node, str):
        yield path, node


def leaf_name(path):
    """配列添字を落とした末尾のキー名（airbnb_url / license.number など）を返す。"""
    cleaned = re.sub(r"\[\d+\]", "", path)
    parts = cleaned.split(".")
    if len(parts) >= 2:
        return parts[-2] + "." + parts[-1], parts[-1]
    return cleaned, cleaned


def validate_schema(doc):
    if not check_type("(root)", doc, dict, "オブジェクト"):
        return
    if doc.get("schema_version") != 1:
        err("schema_version", "1 であるべき（実際: %r）" % doc.get("schema_version"))
    rooms = doc.get("rooms")
    if not check_type("rooms", rooms, list, "配列"):
        return
    if not rooms:
        err("rooms", "1室以上必要")
        return

    seen_codes = set()
    for index, room in enumerate(rooms):
        base = "rooms[%d]" % index
        if not check_type(base, room, dict, "オブジェクト"):
            continue
        code = room.get("code")
        if not isinstance(code, str) or not ROOM_CODE_RE.match(code):
            err(base + ".code", "EVT + 3桁であるべき（実際: %r）" % code)
        else:
            if code in seen_codes:
                err(base + ".code", "部屋コードが重複している: %s" % code)
            seen_codes.add(code)
            base = "rooms[%s]" % code

        for key, types, label in [
            ("name", str, "文字列"),
            ("active", bool, "真偽値"),
            ("desc", str, "文字列"),
            ("beds", str, "文字列"),
            ("size", str, "文字列"),
            ("badge", str, "文字列"),
            ("reviews", int, "整数"),
        ]:
            if key not in room:
                err("%s.%s" % (base, key), "必須項目が無い")
            else:
                check_type("%s.%s" % (base, key), room[key], types, label)

        capacity = room.get("capacity")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or not (1 <= capacity <= 10):
            err(base + ".capacity", "1〜10の整数であるべき（実際: %r）" % capacity)

        rating = room.get("rating")
        if not isinstance(rating, (int, float)) or isinstance(rating, bool) or not (0 <= rating <= 5):
            err(base + ".rating", "0〜5の数値であるべき（実際: %r）" % rating)

        for key in ("layout", "amenities"):
            block = room.get(key)
            if not isinstance(block, dict):
                err("%s.%s" % (base, key), "en / ja を持つオブジェクトであるべき")
                continue
            for lang in ("en", "ja"):
                if lang not in block:
                    err("%s.%s.%s" % (base, key, lang), "必須項目が無い")

        photo = room.get("photo")
        if not isinstance(photo, dict):
            err(base + ".photo", "オブジェクトであるべき")
        else:
            path = photo.get("file")
            if not isinstance(path, str) or not PHOTO_RE.match(path):
                err(base + ".photo.file", "images/rooms/evtNNN.png 形式であるべき（実際: %r）" % path)
            elif not os.path.exists(os.path.join(ROOT, path)):
                err(base + ".photo.file", "ファイルが実在しない: %s" % path)
            alt = photo.get("alt")
            if not isinstance(alt, dict) or not alt.get("en") or not alt.get("ja"):
                err(base + ".photo.alt", "en / ja の alt 文が必要")

        url = room.get("airbnb_url")
        if not isinstance(url, str) or not AIRBNB_RE.match(url):
            err(base + ".airbnb_url", "https://www.airbnb.jp/rooms/<数字> であるべき（実際: %r）" % url)

        lic = room.get("license")
        if not isinstance(lic, dict):
            err(base + ".license", "オブジェクトであるべき")
        else:
            number = lic.get("number")
            if not isinstance(number, str) or not LICENSE_RE.match(number):
                err(base + ".license.number", "許可番号の形式が不正（実際: %r）" % number)
            if lic.get("display") is not False:
                # 表示のON/OFFは井口さんの裁定。勝手にONにしない。
                err(base + ".license.display",
                    "false であるべき（表示の可否は未裁定。変更するときは指示を得ること）")


def validate_forbidden(doc):
    for path, text in walk_strings(doc):
        lowered = text.lower()
        for word in FORBIDDEN_WORDS:
            if word in lowered:
                err(path, "公開できない語が含まれている: %r" % word)

        pair, last = leaf_name(path)
        if pair in DIGIT_ALLOWED_PATHS or last in DIGIT_ALLOWED_PATHS:
            continue
        for run in DIGIT_RUN_RE.findall(text):
            if YEARLIKE_RE.match(run):
                continue
            err(path, "4桁以上の数字列は暗証番号の混入を疑うため禁止: %r" % run)


def main():
    if not os.path.exists(ROOMS_JSON):
        print("NG: data/rooms.json が無い")
        return 1
    try:
        with io.open(ROOMS_JSON, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as exc:
        print("NG: JSON として読めない: %s" % exc)
        return 1

    validate_schema(doc)
    validate_forbidden(doc)

    if errors:
        print("NG: %d 件" % len(errors))
        for line in errors:
            print("  - " + line)
        return 1
    rooms = doc.get("rooms", [])
    active = [r["code"] for r in rooms if r.get("active")]
    print("OK: %d rooms (active: %s)" % (len(rooms), ", ".join(active) or "なし"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
