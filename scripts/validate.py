#!/usr/bin/env python3
"""data/rooms.json と生成物の検査。

やること:
  1. スキーマ検証        …… 必要な項目が、想定した型・値で入っているか
  2. 禁止パターン検査    …… 公開してはいけない値が紛れ込んでいないか
  3. 構造化データ検査    …… JSON-LD の中身が実物と食い違っていないか
  4. hreflang 相互参照   …… 英日ページが互いを正しく指しているか

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
# 許可番号は「7葛保生環令17号」の形。桁数が増える将来も見越して 1〜4 桁を許す。
LICENSE_RE = re.compile(r"^\d{1,2}葛保生環令\d{1,4}号$")
# 面積は「36 m²」の形（半角数字・半角スペース・m²）。表記を一本化しておかないと
# 構造化データの floorSize に数値として渡せない。
SIZE_RE = re.compile(r"^(\d{1,3}(?:\.\d)?) m²$")
SIZE_MIN, SIZE_MAX = 10.0, 100.0

FORBIDDEN_WORDS = [
    "password", "passwd", "passcode", "pincode", "pin code",
    "secret", "token", "api_key", "apikey", "access_key", "private_key",
    "credential", "smartlock", "keybox", "lockbox",
    "暗証", "パスワード", "解錠", "鍵番号", "キーボックス",
    "社内", "内部メモ", "非公開", "ゲスト名", "宿泊者名", "予約番号",
]

# 4桁以上の数字列は原則として疑う（暗証番号・キーボックス番号の混入を防ぐため）。
DIGIT_RUN_RE = re.compile(r"\d{4,}")
DIGIT_ALLOWED_PATHS = {"airbnb_url", "license.number", "photo.file", "map_url"}
YEARLIKE_RE = re.compile(r"^(1[0-9]{3}|2[0-9]{3})$")

# 構造化データに出してはいけないプロパティ。
# いずれも現時点で裏づけとなる原典が無く、誤情報の掲載を構造上できなくするため弾く。
FORBIDDEN_LD_PROPS = ["aggregateRating", "review", "ratingValue", "reviewCount",
                      "starRating", "priceRange", "offers", "price"]

errors = []


def err(path, msg):
    errors.append("%s: %s" % (path, msg))


def check_type(path, value, types, label):
    if not isinstance(value, types):
        err(path, "%s であるべき（実際: %s）" % (label, type(value).__name__))
        return False
    return True


# ----------------------------------------------------------------- rooms.json

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
    cleaned = re.sub(r"\[\d+\]", "", path)
    parts = cleaned.split(".")
    if len(parts) >= 2:
        return parts[-2] + "." + parts[-1], parts[-1]
    return cleaned, cleaned


def check_bilingual(base, block, key):
    if not isinstance(block, dict):
        err("%s.%s" % (base, key), "en / ja を持つオブジェクトであるべき")
        return False
    ok = True
    for lang in ("en", "ja"):
        if lang not in block:
            err("%s.%s.%s" % (base, key, lang), "必須項目が無い")
            ok = False
    return ok


def validate_site(doc):
    site = doc.get("site")
    if not check_type("site", site, dict, "オブジェクト"):
        return
    if not site.get("base_url", "").startswith("https://"):
        err("site.base_url", "https:// で始まる URL であるべき")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(site.get("last_updated", ""))):
        err("site.last_updated", "YYYY-MM-DD であるべき")

    biz = site.get("business")
    if not check_type("site.business", biz, dict, "オブジェクト"):
        return
    for key in ("name", "operator"):
        if not biz.get(key):
            err("site.business.%s" % key, "必須項目が空")
    for key in ("license_type", "license_authority", "nearest_station"):
        check_bilingual("site.business", biz.get(key), key)

    addr = biz.get("address")
    if not check_type("site.business.address", addr, dict, "オブジェクト"):
        return
    for key in ("region", "locality", "street", "full"):
        check_bilingual("site.business.address", addr.get(key), key)
    # 住所の分解ミス・表記ゆれを止める（連結一致）
    if all(isinstance(addr.get(k), dict) for k in ("region", "locality", "street", "full")):
        joined = addr["region"]["ja"] + addr["locality"]["ja"] + addr["street"]["ja"]
        if joined != addr["full"]["ja"]:
            err("site.business.address.full.ja",
                "region+locality+street の連結と一致しない（%r != %r）" % (addr["full"]["ja"], joined))

    geo = biz.get("geo")
    if geo is not None:
        if not isinstance(geo, dict):
            err("site.business.geo", "オブジェクトか null であるべき")
        else:
            lat, lon = geo.get("latitude"), geo.get("longitude")
            if lat is not None or lon is not None:
                if not isinstance(lat, (int, float)) or not (-90 <= lat <= 90):
                    err("site.business.geo.latitude", "-90〜90 の数値であるべき（実際: %r）" % lat)
                if not isinstance(lon, (int, float)) or not (-180 <= lon <= 180):
                    err("site.business.geo.longitude", "-180〜180 の数値であるべき（実際: %r）" % lon)

    for key in ("checkin", "checkout"):
        if not re.match(r"^\d{2}:\d{2}$", str(biz.get(key, ""))):
            err("site.business.%s" % key, "HH:MM であるべき（実際: %r）" % biz.get(key))

    for i, p in enumerate(biz.get("images", [])):
        if not os.path.exists(os.path.join(ROOT, p)):
            err("site.business.images[%d]" % i, "ファイルが実在しない: %s" % p)


def validate_rooms(doc):
    if doc.get("schema_version") != 2:
        err("schema_version", "2 であるべき（実際: %r）" % doc.get("schema_version"))
    rooms = doc.get("rooms")
    if not check_type("rooms", rooms, list, "配列"):
        return
    if not rooms:
        err("rooms", "1室以上必要")
        return

    seen = set()
    for index, room in enumerate(rooms):
        base = "rooms[%d]" % index
        if not check_type(base, room, dict, "オブジェクト"):
            continue
        code = room.get("code")
        if not isinstance(code, str) or not ROOM_CODE_RE.match(code):
            err(base + ".code", "EVT + 3桁であるべき（実際: %r）" % code)
        else:
            if code in seen:
                err(base + ".code", "部屋コードが重複している: %s" % code)
            seen.add(code)
            base = "rooms[%s]" % code

        if not isinstance(room.get("active"), bool):
            err(base + ".active", "真偽値であるべき")
        if not isinstance(room.get("reviews"), int) or isinstance(room.get("reviews"), bool):
            err(base + ".reviews", "整数であるべき")
        # 面積は「まだ確認できていない」状態を空文字で表せるようにしておく。
        # 裏づけの無い数値を置くより、空のままの方が正しい（生成側も空なら出力しない）。
        # ただし値を入れるなら、表記と値域は機械で縛る。
        size = room.get("size")
        if not isinstance(size, str):
            err(base + ".size", "文字列であるべき（未確定なら空文字）")
        elif size:
            m = SIZE_RE.match(size)
            if not m:
                err(base + ".size", "「36 m²」の形であるべき（実際: %r）" % size)
            else:
                value = float(m.group(1))
                if not (SIZE_MIN <= value <= SIZE_MAX):
                    err(base + ".size",
                        "%g〜%g m² の範囲であるべき（実際: %g）"
                        % (SIZE_MIN, SIZE_MAX, value))

        floor = room.get("floor")
        if not isinstance(floor, int) or isinstance(floor, bool) or not (1 <= floor <= 20):
            err(base + ".floor", "1〜20 の整数であるべき（実際: %r）" % floor)

        cap = room.get("capacity")
        if not isinstance(cap, int) or isinstance(cap, bool) or not (1 <= cap <= 10):
            err(base + ".capacity", "1〜10 の整数であるべき（実際: %r）" % cap)

        rating = room.get("rating")
        if not isinstance(rating, (int, float)) or isinstance(rating, bool) or not (0 <= rating <= 5):
            err(base + ".rating", "0〜5 の数値であるべき（実際: %r）" % rating)

        for key in ("name", "layout", "beds", "desc", "badge", "amenities"):
            check_bilingual(base, room.get(key), key)

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
            if not isinstance(lic.get("display"), bool):
                err(base + ".license.display", "真偽値であるべき")


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


# ----------------------------------------------------------------- 生成物

def page_list(doc):
    """検査対象のページ（相対パス, 言語, 部屋 or None）。"""
    rooms = [r for r in doc["rooms"] if r.get("active")]
    pages = [("index.html", "en", None), ("ja.html", "ja", None)]
    for r in rooms:
        f = "%s.html" % r["code"].lower()
        pages.append(("rooms/" + f, "en", r))
        pages.append(("rooms/ja/" + f, "ja", r))
    return rooms, pages


def read_page(rel):
    path = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def extract_graph(html, rel):
    """そのページの @graph を返す（FAQPage など単体ノードのブロックは無視する）。"""
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    graphs = []
    for b in blocks:
        try:
            obj = json.loads(b.replace("\\u003c", "<"))
        except ValueError as exc:
            err(rel, "JSON-LD が読めない: %s" % exc)
            continue
        if isinstance(obj, dict) and "@graph" in obj:
            if obj.get("@context") != "https://schema.org":
                err(rel, "@context が https://schema.org でない")
            if not isinstance(obj["@graph"], list):
                err(rel, "@graph が配列でない")
            else:
                graphs.append(obj["@graph"])
    if len(graphs) != 1:
        err(rel, "@graph を持つ JSON-LD ブロックがちょうど1つであるべき（実際: %d）" % len(graphs))
        return None
    return graphs[0]


def find_nodes(graph, typ):
    return [n for n in graph if n.get("@type") == typ]


def local_path_of(base, url):
    """base 配下の絶対URLをリポジトリ内の相対パスに直す。外部URLなら None。"""
    if not url.startswith(base + "/"):
        return None
    rel = url[len(base) + 1:].split("#")[0].split("?")[0]
    return rel or "index.html"


def expected_floor_size(size):
    """rooms.json の size から、生成物に出ているはずの floorSize を組み立てる。

    生成側（generate.py の floor_size_node）と同じ形を独立に作る。
    片方を書き換えたらここで食い違いとして落ちる。
    """
    if not isinstance(size, str) or not size:
        return None
    m = SIZE_RE.match(size)
    if not m:
        return None
    value = float(m.group(1))
    return {"@type": "QuantitativeValue",
            "value": int(value) if value.is_integer() else value,
            "unitCode": "MTK"}


def validate_generated(doc):
    base = doc["site"]["base_url"]
    rooms, pages = page_list(doc)
    lodging_seen = {}
    size_by_code = {r["code"]: expected_floor_size(r.get("size"))
                    for r in doc.get("rooms", []) if isinstance(r, dict) and r.get("code")}

    for rel, lang, room in pages:
        html = read_page(rel)
        if html is None:
            err(rel, "生成されていない（scripts/generate.py を実行すること）")
            continue

        # --- hreflang の相互参照 ---
        canon = re.findall(r'<link rel="canonical" href="([^"]+)"', html)
        en = re.findall(r'<link rel="alternate" hreflang="en" href="([^"]+)"', html)
        ja = re.findall(r'<link rel="alternate" hreflang="ja" href="([^"]+)"', html)
        xd = re.findall(r'<link rel="alternate" hreflang="x-default" href="([^"]+)"', html)
        for name, got in [("canonical", canon), ("hreflang=en", en),
                          ("hreflang=ja", ja), ("hreflang=x-default", xd)]:
            if len(got) != 1:
                err(rel, "%s がちょうど1つであるべき（実際: %d）" % (name, len(got)))
        if canon and en and ja:
            want_self = en[0] if lang == "en" else ja[0]
            if canon[0] != want_self:
                err(rel, "canonical が自ページを指していない（%s）" % canon[0])
            if xd and xd[0] != en[0]:
                err(rel, "x-default は英語ページを指すべき（%s）" % xd[0])
            # 相手側から自分が指されているか
            other_rel = local_path_of(base, ja[0] if lang == "en" else en[0])
            other_html = read_page(other_rel) if other_rel else None
            if other_html is None:
                err(rel, "対の言語ページが実在しない: %s" % other_rel)
            else:
                back = re.findall(r'<link rel="canonical" href="([^"]+)"', other_html)
                want_other = ja[0] if lang == "en" else en[0]
                if not back or back[0] != want_other:
                    err(rel, "対の言語ページの canonical がこちらを指していない")

        # --- Matomo タグ ---
        if "_paq.push(['setSiteId', '4'])" not in html:
            err(rel, "Matomo の setSiteId '4' が無い")
        if "_paq.push(['disableCookies'])" not in html:
            err(rel, "Matomo の disableCookies が無い")
        if "'trackEvent', 'airbnb', 'click'" not in html:
            err(rel, "Airbnb クリックの trackEvent が無い")
        if 'class="btn btn-book"' not in html and "btn btn-book btn-primary" not in html:
            err(rel, "予約ボタン（btn-book）が無い")

        # --- 構造化データ ---
        graph = extract_graph(html, rel)
        if graph is None:
            continue

        raw = json.dumps(graph, ensure_ascii=False)
        for prop in FORBIDDEN_LD_PROPS:
            if '"%s"' % prop in raw:
                err(rel, "構造化データに禁止プロパティが含まれている: %s" % prop)

        lodgings = find_nodes(graph, "LodgingBusiness")
        if len(lodgings) != 1:
            err(rel, "LodgingBusiness がちょうど1つであるべき（実際: %d）" % len(lodgings))
        else:
            node = lodgings[0]
            if node.get("@id") != base + "/#lodging":
                err(rel, "LodgingBusiness の @id が %s/#lodging でない" % base)
            lodging_seen[rel] = json.dumps(node, ensure_ascii=False, sort_keys=True)
            addr = node.get("address", {})
            want = doc["site"]["business"]["address"]
            joined = (addr.get("addressRegion", "") + addr.get("addressLocality", "")
                      + addr.get("streetAddress", ""))
            if joined != want["full"]["ja"]:
                err(rel, "JSON-LD の住所分解が rooms.json の full と一致しない（%r）" % joined)
            if node.get("numberOfRooms") != len(rooms):
                err(rel, "numberOfRooms が active な部屋数と違う（%r != %d）"
                    % (node.get("numberOfRooms"), len(rooms)))

        hotel_rooms = find_nodes(graph, "HotelRoom")
        if room is None:
            if len(hotel_rooms) != len(rooms):
                err(rel, "HotelRoom の数が active な部屋数と違う（%d != %d）"
                    % (len(hotel_rooms), len(rooms)))
            active_codes = set(r["code"] for r in rooms)
            for n in hotel_rooms:
                for m in re.findall(r"EVT\d{3}", n.get("name", "")):
                    if m not in active_codes:
                        err(rel, "active でない部屋が構造化データに出ている: %s" % m)
        else:
            if len(hotel_rooms) != 1:
                err(rel, "個別ページの HotelRoom は1つであるべき（実際: %d）" % len(hotel_rooms))
            elif canon:
                if hotel_rooms[0].get("url") != canon[0]:
                    err(rel, "HotelRoom.url が canonical と一致しない")
            crumbs = find_nodes(graph, "BreadcrumbList")
            if len(crumbs) != 1:
                err(rel, "BreadcrumbList がちょうど1つであるべき（実際: %d）" % len(crumbs))
            else:
                visible = re.search(r'<nav class="breadcrumb".*?</nav>', html, re.S)
                vis_text = re.sub(r"<[^>]+>", " ", visible.group(0)) if visible else ""
                for item in crumbs[0].get("itemListElement", []):
                    if item.get("name") not in vis_text:
                        err(rel, "パンくずの名前が可視表示に無い: %r" % item.get("name"))

        for n in hotel_rooms:
            if n.get("containedInPlace", {}).get("@id") != base + "/#lodging":
                err(rel, "HotelRoom.containedInPlace が施設ノードを指していない")

            # floorSize は rooms.json の size と一対一で対応させる。
            # 面積が空の部屋に floorSize が出る＝出典の無い数値を公開することなので、
            # 「出ていないこと」も含めて検査する。
            codes = re.findall(r"EVT\d{3}", n.get("name", ""))
            expected = size_by_code.get(codes[0]) if codes else None
            actual = n.get("floorSize")
            if expected is None:
                if actual is not None:
                    err(rel, "面積が未確定の部屋に floorSize が出ている: %s"
                        % (codes[0] if codes else "?"))
            elif actual != expected:
                err(rel, "HotelRoom.floorSize が rooms.json と一致しない: %s（%r != %r）"
                    % (codes[0] if codes else "?", actual, expected))

        # --- 絶対URLの実在確認（自サイト内のみ）---
        for url in set(re.findall(r'"(https://[^"]+)"', raw)):
            p = local_path_of(base, url)
            if p is None:
                continue
            if not os.path.exists(os.path.join(ROOT, p.replace("/", os.sep))):
                err(rel, "構造化データが実在しないパスを指している: %s" % p)

    # LodgingBusiness は全ページで完全に同一であること（実体はひとつだから）
    if len(set(lodging_seen.values())) > 1:
        err("(all pages)", "LodgingBusiness ノードがページ間で一致しない（%d 種類）"
            % len(set(lodging_seen.values())))


def validate_sitemap(doc):
    base = doc["site"]["base_url"]
    rooms, pages = page_list(doc)
    path = os.path.join(ROOT, "sitemap.xml")
    if not os.path.exists(path):
        err("sitemap.xml", "生成されていない")
        return
    with io.open(path, encoding="utf-8") as fh:
        xml = fh.read()
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    want = set()
    for rel, _lang, _room in pages:
        want.add(base + "/" if rel == "index.html" else base + "/" + rel)
    if set(locs) != want:
        for extra in sorted(set(locs) - want):
            err("sitemap.xml", "余計な URL: %s" % extra)
        for miss in sorted(want - set(locs)):
            err("sitemap.xml", "足りない URL: %s" % miss)
    inactive = [r["code"].lower() for r in doc["rooms"] if not r.get("active")]
    for code in inactive:
        if code in xml:
            err("sitemap.xml", "active でない部屋が残っている: %s" % code)

    rpath = os.path.join(ROOT, "robots.txt")
    if not os.path.exists(rpath):
        err("robots.txt", "生成されていない")
    else:
        with io.open(rpath, encoding="utf-8") as fh:
            robots = fh.read()
        if ("Sitemap: %s/sitemap.xml" % base) not in robots:
            err("robots.txt", "Sitemap: 行が無い")


def validate_no_stale_pages(doc):
    """active でなくなった部屋のページが残っていないか。"""
    _rooms, pages = page_list(doc)
    expected = set(rel for rel, _l, _r in pages)
    rooms_dir = os.path.join(ROOT, "rooms")
    if not os.path.isdir(rooms_dir):
        return
    for dirpath, _dirs, names in os.walk(rooms_dir):
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/")
            if rel not in expected:
                err(rel, "生成対象でないページが残っている（停止した部屋の消し忘れ）")


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

    validate_rooms(doc)
    validate_site(doc)
    validate_forbidden(doc)
    if not errors:
        # データが壊れている状態で生成物を見ても意味がないので、通ってから検査する
        validate_generated(doc)
        validate_sitemap(doc)
        validate_no_stale_pages(doc)

    if errors:
        print("NG: %d 件" % len(errors))
        for line in errors:
            print("  - " + line)
        return 1

    rooms = doc.get("rooms", [])
    active = [r["code"] for r in rooms if r.get("active")]
    print("OK: %d rooms (active: %s) / %d pages"
          % (len(rooms), ", ".join(active) or "なし", 2 + len(active) * 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
