#!/usr/bin/env python3
"""data/rooms.json と templates/index.html.tmpl から index.html を生成する。

使い方:
    python scripts/generate.py            # index.html を書き出す
    python scripts/generate.py --check    # 書き出さず、現行 index.html との差分だけ見る

生成物は現行 index.html と「改行を LF に正規化したうえで」一致することを CI で検査する。
テンプレートに差し込むのは次の2か所だけ:
    {{ROOMS_JS}}    …… data/rooms.json の active な部屋から作る JS 配列リテラル
    {{MATOMO_TAG}}  …… </head> 直前に入る Matomo 計測タグ
"""
import argparse
import difflib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOMS_JSON = os.path.join(ROOT, "data", "rooms.json")
TEMPLATE = os.path.join(ROOT, "templates", "index.html.tmpl")
OUTPUT = os.path.join(ROOT, "index.html")

# Matomo は既存インスタンスへ相乗りする（site_id=4 = e-VILLAGE TATEISHI）。
MATOMO_URL = "https://iguchi-koumusyo.com/analytics/"
MATOMO_SITE_ID = "4"

# 予約ボタンのラベルは "Book EVT101 on Airbnb" 形式なので、そこから部屋コードを取り出す。
# テンプレート本体に手を入れずに計測できるよう、クリックは document 側で拾う。
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


def js_string(value):
    """JS の文字列リテラルとして安全に書き出す（現行 index.html と同じ二重引用符）。"""
    return json.dumps(value, ensure_ascii=False)


def build_rooms_js(rooms):
    """現行 index.html の ROOMS 配列と同じ書式で JS 配列リテラルを組み立てる。

    書式を現行に合わせているのは、生成物と現行を1文字単位で突き合わせられるようにするため。
    """
    out = ["const ROOMS = ["]
    for room in rooms:
        out.append("  {")
        out.append('    code:%s, name:%s,' % (js_string(room["code"]), js_string(room["name"])))
        out.append('    rating:%s, reviews:%s, badge:%s,' % (
            room["rating"], room["reviews"], js_string(room["badge"])))
        out.append('    guests:%s, beds:%s, size:%s,' % (
            room["capacity"], js_string(room["beds"]), js_string(room["size"])))
        out.append('    desc:%s,' % js_string(room["desc"]))
        out.append('    photo:%s,' % js_string(room["photo"]["file"]))
        out.append('    url:%s' % js_string(room["airbnb_url"]))
        out.append("  },")
    if len(out) > 1:
        out[-1] = "  }"  # 最後の要素は末尾カンマを付けない
    out.append("];")
    return "\n".join(out)


def render():
    with io.open(ROOMS_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    rooms = [r for r in data["rooms"] if r.get("active", False)]
    with io.open(TEMPLATE, encoding="utf-8", newline="") as fh:
        # 改行は LF に揃えてから扱う。Windows で編集されても出力が変わらないようにするため。
        tmpl = normalize(fh.read())
    html = tmpl.replace("{{ROOMS_JS}}", build_rooms_js(rooms))
    html = html.replace("{{MATOMO_TAG}}", MATOMO_TAG)
    return html, rooms


def normalize(text):
    """改行だけを揃える。比較はこの形で行う。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="書き出さずに現行 index.html との差分を表示する")
    ap.add_argument("--baseline", default=OUTPUT,
                    help="--check で突き合わせる相手（既定は index.html）")
    args = ap.parse_args()

    html, rooms = render()

    if not args.check:
        with io.open(OUTPUT, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(html)
        print("generated %s (%d active rooms: %s)" % (
            os.path.relpath(OUTPUT, ROOT), len(rooms), ", ".join(r["code"] for r in rooms)))
        return 0

    with io.open(args.baseline, encoding="utf-8", newline="") as fh:
        current = fh.read()
    diff = list(difflib.unified_diff(
        normalize(current).splitlines(True),
        normalize(html).splitlines(True),
        fromfile="current/" + os.path.basename(args.baseline),
        tofile="generated/index.html"))
    if not diff:
        print("OK: generated output matches %s (after newline normalization)"
              % os.path.basename(args.baseline))
        return 0
    sys.stdout.writelines(diff)
    print("\nNG: %d diff lines" % len(diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
