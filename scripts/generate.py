#!/usr/bin/env python3
"""data/rooms.json から公開ページ一式を生成する。

出力:
    index.html                英語トップ
    ja.html                   日本語トップ
    rooms/<code>.html         英語・客室個別（5室）
    rooms/ja/<code>.html      日本語・客室個別（5室）
    sitemap.xml
    robots.txt

使い方:
    python scripts/generate.py            生成する
    python scripts/generate.py --check    生成せず、既存ファイルとの差分だけ見る

依存ライブラリは無い（Python 3 標準ライブラリのみ）。同じ入力からは常に同じバイト列が出る。
`active: false` の部屋は、トップの一覧・個別ページ・sitemap・構造化データの
どこにも現れない（ファイル自体を消す）。
"""
import argparse
import difflib
import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "rooms.json")
TPL = os.path.join(ROOT, "templates")

# Matomo は既存インスタンスへ相乗りする（site_id=4 = e-VILLAGE TATEISHI）。
MATOMO_URL = "https://iguchi-koumusyo.com/analytics/"
MATOMO_SITE_ID = "4"

MATOMO_TAG = r"""
<!-- Matomo -->
<script>
  var _paq = window._paq = window._paq || [];
  /* Cookie を使わない設定。同意バナーなしで計測するため */
  _paq.push(['disableCookies']);
  _paq.push(['trackPageView']);
  _paq.push(['enableLinkTracking']);
  (function () {
    var u = "%(url)s";
    _paq.push(['setTrackerUrl', u + 'matomo.php']);
    _paq.push(['setSiteId', '%(site_id)s']);
    var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];
    g.async = true; g.src = u + 'matomo.js'; s.parentNode.insertBefore(g, s);
  })();
  /* Airbnb 予約ボタンのクリックを airbnb / click / 部屋コード で記録する */
  document.addEventListener('click', function (e) {
    var t = e.target;
    var a = t && t.closest ? t.closest('a.btn-book') : null;
    if (!a) return;
    var m = (a.textContent || '').match(/EVT\d+/);
    if (m) _paq.push(['trackEvent', 'airbnb', 'click', m[0]]);
  });
</script>
<!-- End Matomo Code -->
""" % {"url": MATOMO_URL, "site_id": MATOMO_SITE_ID}

# トップページの観光地カード。部屋と違い運用で動かないので、ここに置いている。
ATTRACTIONS = [
    {"photo": "images/attractions/skytree.jpg", "time": {"en": "10 min", "ja": "10分"},
     "name": {"en": "Tokyo Skytree", "ja": "東京スカイツリー"},
     "desc": {"en": "Japan's tallest tower in Sumida, with sweeping city views and the Solamachi shopping complex at its base.",
              "ja": "墨田区にある日本一高いタワー。展望台からの眺めと、足元の商業施設ソラマチが楽しめます。"}},
    {"photo": "images/attractions/asakusa.jpg", "time": {"en": "25 min", "ja": "25分"},
     "name": {"en": "Asakusa", "ja": "浅草"},
     "desc": {"en": "Senso-ji Temple, the Kaminari-mon gate and old-Tokyo streets full of food stalls and craft shops.",
              "ja": "浅草寺と雷門、食べ歩きや職人の店が並ぶ下町の通り。"}},
    {"photo": "images/attractions/shibamata.jpg", "time": {"en": "15 min", "ja": "15分"},
     "name": {"en": "Shibamata Taishakuten", "ja": "柴又帝釈天"},
     "desc": {"en": "A nostalgic temple town in Katsushika, loved for beautiful wood carvings and a retro shopping street.",
              "ja": "葛飾区の門前町。彫刻ギャラリーと、昔ながらの参道の店並びで知られています。"}},
    {"photo": "images/attractions/ginza.jpg", "time": {"en": "30 min", "ja": "30分"},
     "name": {"en": "Ginza", "ja": "銀座"},
     "desc": {"en": "Tokyo's elegant shopping district, known for department stores, galleries, restaurants and the Wako clock tower.",
              "ja": "百貨店やギャラリー、飲食店が集まる街。和光の時計塔が目印です。"}},
    {"photo": "images/attractions/shibuya.jpg", "time": {"en": "50 min", "ja": "50分"},
     "name": {"en": "Shibuya", "ja": "渋谷"},
     "desc": {"en": "The famous scramble crossing, nightlife and youth culture at the heart of modern Tokyo.",
              "ja": "スクランブル交差点で知られる、若者文化とナイトライフの中心地。"}},
]


# ----------------------------------------------------------------------------- 小道具

def esc(s):
    """HTML のテキスト・属性値として安全にする。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def read_json():
    with io.open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def read_tpl(name):
    with io.open(os.path.join(TPL, name), encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def active_rooms(data):
    return [r for r in data["rooms"] if r.get("active", False)]


# rooms.json の「36 m²」を構造化データの数値へ変換するための形。
# validate.py の SIZE_RE と同じ形を見ている（片方だけ緩めないこと）。
SIZE_RE = re.compile(r"^(\d{1,3}(?:\.\d)?) m²$")


def floor_size_node(size):
    """schema.org の floorSize を作る。値が無い・読めないときは None。

    出典の確認できた値だけを構造化データに出す。空文字（面積未確認）の部屋で
    0 や推定値を出さないために、ここで黙って握りつぶさず None を返して呼び側で落とす。
    unitCode の MTK は UN/CEFACT の平方メートル。
    """
    if not size:
        return None
    m = SIZE_RE.match(size)
    if not m:
        return None
    value = float(m.group(1))
    return {"@type": "QuantitativeValue",
            "value": int(value) if value.is_integer() else value,
            "unitCode": "MTK"}


def abs_url(base, path):
    return base + "/" + path.lstrip("/")


# ----------------------------------------------------------------------------- 部品

def hreflang_block(base, en_path, ja_path, self_path):
    """canonical と hreflang。英日で同じ組を出し、x-default は英語に向ける。"""
    return (
        '<link rel="canonical" href="%s" />\n'
        '<link rel="alternate" hreflang="en" href="%s" />\n'
        '<link rel="alternate" hreflang="ja" href="%s" />\n'
        '<link rel="alternate" hreflang="x-default" href="%s" />'
        % (abs_url(base, self_path), abs_url(base, en_path),
           abs_url(base, ja_path), abs_url(base, en_path))
    )


def attractions_html(lang):
    out = []
    for a in ATTRACTIONS:
        out.append(
            '        <article class="spot">\n'
            '          <div class="ph" style="background-image:linear-gradient(rgba(27,42,74,.08),'
            "rgba(27,42,74,.18)),url('%s')\">\n"
            '            <span class="t-badge">🚃 %s</span>\n'
            '          </div>\n'
            '          <div class="b">\n'
            '            <h3>%s</h3>\n'
            '            <p>%s</p>\n'
            '          </div>\n'
            '        </article>\n'
            % (esc(a["photo"]), esc(a["time"][lang]), esc(a["name"][lang]), esc(a["desc"][lang]))
        )
    return "".join(out)


def room_meta(room, lang):
    """カードに出す「定員 / ベッド / 広さ」。空の項目は出さない。"""
    parts = []
    if lang == "en":
        parts.append("Up to <b>%d</b> guests" % room["capacity"])
    else:
        parts.append("定員 <b>%d</b> 名" % room["capacity"])
    if room["beds"][lang]:
        parts.append(esc(room["beds"][lang]))
    if room["size"]:
        parts.append(esc(room["size"]))
    return "".join("<span>%s</span>" % p for p in parts)


def rooms_html(rooms, lang):
    """トップページの客室カード一覧。個別ページへの導線を持つ。"""
    out = []
    for r in rooms:
        code = r["code"]
        detail = ("rooms/%s.html" % code.lower()) if lang == "en" else ("rooms/ja/%s.html" % code.lower())
        badge = r["badge"][lang]
        # バッジが無い部屋では行ごと出さない（カード内に空行を残さないため）
        badge_html = ('            <span class="badge">%s</span>\n' % esc(badge)) if badge else ""
        rating = ""
        if r["rating"]:
            reviews = ""
            if r["reviews"]:
                reviews = ('<span class="n">· %d reviews</span>' % r["reviews"]) if lang == "en" \
                    else ('<span class="n">· レビュー%d件</span>' % r["reviews"])
            rating = '<div class="rating"><span class="star">★</span><b>%s</b>%s</div>' % (
                r["rating"], reviews)
        if lang == "en":
            book = "Book %s on Airbnb" % code
            more = "Room details"
        else:
            book = "%s をAirbnbで予約" % code
            more = "部屋の詳細"
        out.append(
            '        <article class="card">\n'
            '          <div class="photo" style="background-image:url(\'%s\')">\n'
            '            <span class="code">%s</span>\n'
            '%s'
            '          </div>\n'
            '          <div class="body">\n'
            '            <h3>%s</h3>\n'
            '            %s\n'
            '            <p class="desc">%s</p>\n'
            '            <div class="meta">%s</div>\n'
            '            <div class="cta">\n'
            '              <a class="btn btn-book" href="%s" target="_blank" rel="noopener">%s <span class="arrow">→</span></a>\n'
            '              <a class="btn btn-more" href="%s">%s</a>\n'
            '            </div>\n'
            '          </div>\n'
            '        </article>\n'
            % (esc(r["photo"]["file"]), esc(code), badge_html, esc(r["name"][lang]), rating,
               esc(r["desc"][lang]), room_meta(r, lang), esc(r["airbnb_url"]), esc(book),
               esc(detail), esc(more))
        )
    return "".join(out)


def legal_notice(data, rooms, lang, room=None):
    """フッターの法令に基づく表示（旅館業法）。

    値は data/rooms.json の site.business と各室 license。
    許可日は併記しない（井口さん裁定 2026-08-19）。
    display が false の間は何も出さない。
    """
    biz = data["site"]["business"]
    targets = [room] if room is not None else rooms
    licensed = [r for r in targets if r.get("license", {}).get("display") and r["license"]["number"]]
    if not licensed:
        return ""
    addr = biz["address"]["full"][lang]
    if lang == "ja":
        title = "法令に基づく表示（旅館業法）"
        common = ("施設の名称: %s ／ 営業者: %s ／ 営業の種別: %s ／ 許可: %s ／ 所在地: %s"
                  % (esc(biz["name"]), esc(biz["operator"]), esc(biz["license_type"]["ja"]),
                     esc(biz["license_authority"]["ja"]), esc(addr)))
        nums = " ／ ".join("%s: %s" % (esc(r["code"]), esc(r["license"]["number"])) for r in licensed)
        nums = "許可番号 %s" % nums
    else:
        title = "Legal information (Hotel Business Act)"
        common = ("Facility name: %s / Operator: %s / License type: %s / Licensed by: %s / Address: %s"
                  % (esc(biz["name"]), esc(biz["operator"]), esc(biz["license_type"]["en"]),
                     esc(biz["license_authority"]["en"]), esc(addr)))
        nums = " / ".join("%s: %s" % (esc(r["code"]), esc(r["license"]["number"])) for r in licensed)
        nums = "License numbers: %s" % nums
    return ('    <div class="footer-legal"><p><strong>%s</strong></p><p>%s</p><p>%s</p></div>'
            % (title, common, nums))


# ----------------------------------------------------------------------------- 構造化データ

def jsonld_block(obj):
    """JSON-LD を1ブロックにして返す。

    `</script>` によるブロック早期終了と HTML への注入を防ぐため、`<` を \\u003c に置き換える。
    """
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("<", "\\u003c")
    return '<script type="application/ld+json">%s</script>' % text


def lodging_node(data, rooms):
    """LodgingBusiness。全ページで完全に同一のノードにする（実体はひとつだから）。"""
    base = data["site"]["base_url"]
    biz = data["site"]["business"]
    addr = biz["address"]
    node = {
        "@type": "LodgingBusiness",
        "@id": base + "/#lodging",
        "name": biz["name"],
        "alternateName": biz["alternate_names"],
        "url": base + "/",
        "image": [abs_url(base, p) for p in biz["images"]],
        "address": {
            "@type": "PostalAddress",
            "addressCountry": "JP",
            "addressRegion": addr["region"]["ja"],
            "addressLocality": addr["locality"]["ja"],
            "streetAddress": addr["street"]["ja"],
        },
        "numberOfRooms": len(rooms),
    }
    geo = biz.get("geo") or {}
    if geo.get("latitude") is not None and geo.get("longitude") is not None:
        node["geo"] = {"@type": "GeoCoordinates",
                       "latitude": geo["latitude"], "longitude": geo["longitude"]}
    if biz.get("telephone"):
        node["telephone"] = biz["telephone"]
    if biz.get("map_url"):
        node["hasMap"] = biz["map_url"]
    return node


def room_node(data, room, lang):
    """HotelRoom。そのページの言語の個別ページを指す。"""
    base = data["site"]["base_url"]
    code = room["code"]
    path = ("rooms/%s.html" % code.lower()) if lang == "en" else ("rooms/ja/%s.html" % code.lower())
    url = abs_url(base, path)
    node = {
        "@type": "HotelRoom",
        "@id": url + "#room",
        "name": ("%s — %s" % (code, room["name"][lang])),
        "description": room["desc"][lang],
        "url": url,
        "image": [abs_url(base, room["photo"]["file"])],
        "floorLevel": str(room["floor"]),
        "occupancy": {"@type": "QuantitativeValue", "maxValue": room["capacity"]},
        "containedInPlace": {"@id": base + "/#lodging"},
        "sameAs": room["airbnb_url"],
    }
    floor_size = floor_size_node(room["size"])
    if floor_size is not None:
        node["floorSize"] = floor_size
    return node


def breadcrumb_node(data, room, lang):
    base = data["site"]["base_url"]
    code = room["code"]
    if lang == "en":
        path = "rooms/%s.html" % code.lower()
        items = [("e-village TATEISHI", base + "/"),
                 ("Rooms", base + "/#rooms"),
                 (code, abs_url(base, path))]
    else:
        path = "rooms/ja/%s.html" % code.lower()
        items = [("e-village TATEISHI", base + "/ja.html"),
                 ("客室一覧", base + "/ja.html#rooms"),
                 (code, abs_url(base, path))]
    return {
        "@type": "BreadcrumbList",
        "@id": abs_url(base, path) + "#breadcrumb",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n, "item": u}
            for i, (n, u) in enumerate(items)
        ],
    }


def top_jsonld(data, rooms, lang):
    graph = [lodging_node(data, rooms)]
    graph += [room_node(data, r, lang) for r in rooms]
    return jsonld_block({"@context": "https://schema.org", "@graph": graph})


def room_jsonld(data, rooms, room, lang):
    graph = [lodging_node(data, rooms), room_node(data, room, lang),
             breadcrumb_node(data, room, lang)]
    return jsonld_block({"@context": "https://schema.org", "@graph": graph})


# ----------------------------------------------------------------------------- ページ

def render_top(data, rooms, lang, style):
    base = data["site"]["base_url"]
    tpl = read_tpl("index.template.html" if lang == "en" else "ja.template.html")
    self_path = "" if lang == "en" else "ja.html"
    out = tpl
    out = out.replace("{{STYLE}}", style)
    out = out.replace("{{HREFLANG}}", hreflang_block(base, "", "ja.html", self_path))
    out = out.replace("{{JSONLD}}", top_jsonld(data, rooms, lang))
    out = out.replace("{{MATOMO_TAG}}", MATOMO_TAG)
    out = out.replace("{{ATTRACTIONS}}", attractions_html(lang))
    out = out.replace("{{ROOMS}}", rooms_html(rooms, lang))
    out = out.replace("{{LEGAL_NOTICE}}", legal_notice(data, rooms, lang))
    check_placeholders(out, "top-" + lang)
    return out


def facts_html(room, lang):
    if lang == "en":
        pairs = [("Room", room["code"]),
                 ("Floor", "%dF" % room["floor"]),
                 ("Guests", "up to %d" % room["capacity"]),
                 ("Layout", room["layout"]["en"]),
                 ("Beds", room["beds"]["en"])]
        if room["size"]:
            pairs.append(("Size", room["size"]))
    else:
        pairs = [("部屋", room["code"]),
                 ("階", "%d階" % room["floor"]),
                 ("定員", "%d名まで" % room["capacity"]),
                 ("間取り", room["layout"]["ja"]),
                 ("ベッド", room["beds"]["ja"])]
        if room["size"]:
            pairs.append(("広さ", room["size"]))
    return "".join("            <dt>%s</dt><dd>%s</dd>\n" % (esc(k), esc(v)) for k, v in pairs)


def amenities_html(room, lang):
    return "".join("            <li>%s</li>\n" % esc(a) for a in room["amenities"][lang])


def other_rooms_html(room, rooms, lang):
    out = []
    for r in rooms:
        if r["code"] == room["code"]:
            continue
        href = "%s.html" % r["code"].lower()
        out.append('<a href="%s">%s</a>' % (esc(href), esc(r["code"])))
    return "".join(out)


def render_room(data, rooms, room, lang, style):
    base = data["site"]["base_url"]
    code = room["code"]
    fname = "%s.html" % code.lower()
    en_path = "rooms/%s" % fname
    ja_path = "rooms/ja/%s" % fname
    self_path = en_path if lang == "en" else ja_path
    tpl = read_tpl("room-en.template.html" if lang == "en" else "room-ja.template.html")

    if lang == "en":
        title = "%s %s | e-village TATEISHI" % (code, room["name"]["en"])
    else:
        title = "%s %s｜e-village TATEISHI" % (code, room["name"]["ja"])

    out = tpl
    out = out.replace("{{STYLE}}", style)
    out = out.replace("{{HREFLANG}}", hreflang_block(base, en_path, ja_path, self_path))
    out = out.replace("{{JSONLD}}", room_jsonld(data, rooms, room, lang))
    out = out.replace("{{MATOMO_TAG}}", MATOMO_TAG)
    out = out.replace("{{TITLE}}", esc(title))
    out = out.replace("{{META_DESCRIPTION}}", esc(room["desc"][lang]))
    out = out.replace("{{OG_IMAGE}}", abs_url(base, room["photo"]["file"]))
    out = out.replace("{{FILE}}", fname)
    out = out.replace("{{ROOM_CODE}}", esc(code))
    out = out.replace("{{ROOM_CRUMB}}", esc(code))
    out = out.replace("{{ROOM_NAME}}", esc(room["name"][lang]))
    out = out.replace("{{ROOM_DESC}}", esc(room["desc"][lang]))
    out = out.replace("{{PHOTO}}", esc(room["photo"]["file"]))
    out = out.replace("{{PHOTO_ALT}}", esc(room["photo"]["alt"][lang]))
    out = out.replace("{{AIRBNB_URL}}", esc(room["airbnb_url"]))
    out = out.replace("{{FACTS}}", facts_html(room, lang))
    out = out.replace("{{AMENITIES}}", amenities_html(room, lang))
    out = out.replace("{{OTHER_ROOMS}}", other_rooms_html(room, rooms, lang))
    out = out.replace("{{LEGAL_NOTICE}}", legal_notice(data, rooms, lang, room=room))
    check_placeholders(out, "room-%s-%s" % (code, lang))
    return out


def check_placeholders(text, where):
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise SystemExit("未解決のプレースホルダが残っています: %s %s" % (where, sorted(set(left))))


def render_sitemap(data, rooms):
    base = data["site"]["base_url"]
    lastmod = data["site"]["last_updated"]

    def entry(loc, prio, en_href, ja_href):
        return (
            "  <url>\n"
            "    <loc>%s</loc>\n"
            "    <lastmod>%s</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            "    <priority>%s</priority>\n"
            '    <xhtml:link rel="alternate" hreflang="en" href="%s" />\n'
            '    <xhtml:link rel="alternate" hreflang="ja" href="%s" />\n'
            '    <xhtml:link rel="alternate" hreflang="x-default" href="%s" />\n'
            "  </url>" % (loc, lastmod, prio, en_href, ja_href, en_href)
        )

    entries = [
        entry(base + "/", "1.0", base + "/", base + "/ja.html"),
        entry(base + "/ja.html", "0.9", base + "/", base + "/ja.html"),
    ]
    for r in rooms:
        fname = "%s.html" % r["code"].lower()
        en_url = abs_url(base, "rooms/" + fname)
        ja_url = abs_url(base, "rooms/ja/" + fname)
        entries.append(entry(en_url, "0.8", en_url, ja_url))
        entries.append(entry(ja_url, "0.7", en_url, ja_url))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
            '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
            + "\n".join(entries) + "\n</urlset>\n")


def render_robots(data):
    base = data["site"]["base_url"]
    return ("User-agent: *\n"
            "Allow: /\n"
            "\n"
            "Sitemap: %s/sitemap.xml\n" % base)


# ----------------------------------------------------------------------------- 実行

def build(data):
    """出力すべき (相対パス, 中身) の一覧を作る。"""
    rooms = active_rooms(data)
    style = read_tpl("style.css")
    files = [
        ("index.html", render_top(data, rooms, "en", style)),
        ("ja.html", render_top(data, rooms, "ja", style)),
        ("sitemap.xml", render_sitemap(data, rooms)),
        ("robots.txt", render_robots(data)),
    ]
    for r in rooms:
        fname = "%s.html" % r["code"].lower()
        files.append(("rooms/" + fname, render_room(data, rooms, r, "en", style)))
        files.append(("rooms/ja/" + fname, render_room(data, rooms, r, "ja", style)))
    return rooms, files


def write_all(files, rooms):
    # active でなくなった部屋の個別ページが残らないよう、rooms/ は作り直す。
    rooms_dir = os.path.join(ROOT, "rooms")
    if os.path.isdir(rooms_dir):
        shutil.rmtree(rooms_dir)
    for rel, text in files:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def check_all(files):
    ng = 0
    for rel, text in files:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(path):
            print("NG: %s が無い（生成されていない）" % rel)
            ng += 1
            continue
        with io.open(path, encoding="utf-8", newline="") as fh:
            current = fh.read().replace("\r\n", "\n")
        if current == text:
            continue
        ng += 1
        diff = list(difflib.unified_diff(current.splitlines(True), text.splitlines(True),
                                         fromfile="current/" + rel, tofile="generated/" + rel))
        print("NG: %s に差分 %d 行" % (rel, len(diff)))
        sys.stdout.writelines(diff[:40])
    # 余計なページが残っていないか
    expected = set(rel for rel, _ in files)
    rooms_dir = os.path.join(ROOT, "rooms")
    for dirpath, _dirs, names in os.walk(rooms_dir):
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/")
            if rel not in expected:
                print("NG: 生成対象でないファイルが残っている: %s" % rel)
                ng += 1
    return ng


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="書き出さず差分だけ見る")
    args = ap.parse_args()

    data = read_json()
    rooms, files = build(data)

    if args.check:
        ng = check_all(files)
        if ng:
            print("\nNG: %d 件" % ng)
            return 1
        print("OK: %d ファイルすべて生成結果と一致" % len(files))
        return 0

    write_all(files, rooms)
    print("generated %d files (active rooms: %s)"
          % (len(files), ", ".join(r["code"] for r in rooms)))
    for rel, _ in files:
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
