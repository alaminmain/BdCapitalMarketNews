"""Render the GitHub Pages dashboard (index.html) from market updates."""
from __future__ import annotations

import html
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .config import FEED_DESCRIPTION, FEED_TITLE, GOATCOUNTER_CODE
from .tags import tag_vocabulary

log = logging.getLogger(__name__)

CATEGORY_ORDER = ("Good", "Bad", "Ugly")

# All static UI text in both languages. Content from Gemini (summary,
# description, reason) is bilingual per-row via the BN columns.
UI = {
    "en": {
        "title":        FEED_TITLE,
        "lede":         FEED_DESCRIPTION,
        "last_updated": "Last updated:",
        "rss":          "RSS feed",
        "total":        "Total updates",
        "Good":         "Bullish",
        "Bad":          "Bearish",
        "Ugly":         "Crisis",
        "items":        "items",
        "item":         "item",
        "why":          "Why this matters",
        "source":       "Read source &rarr;",
        "footer_gen":   "Generated automatically",
        "footer_src":   "Source on GitHub",
        "empty":        "No classified items yet — check back soon.",
        "filters":      "Filters",
        "search_ph":    "Search headlines, descriptions, tickers…",
        "date_label":   "Date",
        "date_any":     "Any date",
        "ticker_ph":    "Ticker (e.g. DBH)",
        "all":          "All",
        "tags_label":   "Tags",
        "reset":        "Reset",
        "showing":      "Showing",
        "of":           "of",
        "results":      "results",
        "result":       "result",
        "no_match":     "No items match these filters.",
        "loading":      "Loading history…",
        "blurbs": {
            "Good": "High dividends, structural reforms, strong earnings.",
            "Bad":  "Inflation, weak earnings, index drops, regulatory hits.",
            "Ugly": "Systemic shocks: bank failures, energy crises, defaults.",
        },
    },
    "bn": {
        "title":        "বাংলাদেশ পুঁজিবাজার ও মুদ্রাবাজার — দৈনিক পাল্স",
        "lede":         "ঢাকা স্টক এক্সচেঞ্জ ও বাংলাদেশের মুদ্রাবাজারের অগ্রগতির এআই-নির্বাচিত দৈনিক ভালো / খারাপ / ভয়াবহ মূল্যায়ন।",
        "last_updated": "সর্বশেষ আপডেট:",
        "rss":          "RSS ফিড",
        "total":        "মোট আপডেট",
        "Good":         "তেজি",
        "Bad":          "মন্দা",
        "Ugly":         "সংকট",
        "items":        "টি",
        "item":         "টি",
        "why":          "কেন এটি গুরুত্বপূর্ণ",
        "source":       "মূল উৎস &rarr;",
        "footer_gen":   "স্বয়ংক্রিয়ভাবে তৈরি",
        "footer_src":   "GitHub-এ সোর্স",
        "empty":        "এখনও কোনো শ্রেণিবদ্ধ আইটেম নেই — শীঘ্রই আবার দেখুন।",
        "blurbs": {
            "Good": "উচ্চ লভ্যাংশ, কাঠামোগত সংস্কার, শক্তিশালী আয়।",
            "Bad":  "মূল্যস্ফীতি, দুর্বল আয়, সূচকের পতন, নিয়ন্ত্রক আঘাত।",
            "Ugly": "ব্যবস্থাগত সংকট: ব্যাংক ব্যর্থতা, শক্তি সংকট, খেলাপি।",
        },
        "filters":      "ফিল্টার",
        "search_ph":    "শিরোনাম, বর্ণনা, টিকার খুঁজুন…",
        "date_label":   "তারিখ",
        "date_any":     "যেকোনো তারিখ",
        "ticker_ph":    "টিকার (যেমন DBH)",
        "all":          "সব",
        "tags_label":   "ট্যাগ",
        "reset":        "রিসেট",
        "showing":      "দেখানো হচ্ছে",
        "of":           "এর মধ্যে",
        "results":      "টি ফলাফল",
        "result":       "টি ফলাফল",
        "no_match":     "এই ফিল্টারে কোনো আইটেম মেলেনি।",
        "loading":      "ইতিহাস লোড হচ্ছে…",
    },
}

# DSE-style "TICKER: rest of headline". Allow short names with spaces/dots.
_TICKER_RE = re.compile(r"^([A-Z0-9][A-Z0-9 .&-]{1,18}):\s*(.+)$")


def _split_company(summary: str) -> Tuple[str, str]:
    m = _TICKER_RE.match(summary.strip())
    if not m:
        return "", summary.strip()
    return m.group(1).strip(), m.group(2).strip()


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _goatcounter_snippets(code: str) -> Tuple[str, str]:
    """Build the `<head>` tracking script and the top-bar visitor-count widget.

    Returns ("", "") when no GoatCounter code is configured, so the page
    renders identically to the pre-analytics version.
    """
    code = (code or "").strip()
    if not code:
        return "", ""
    safe = _esc(code)
    head = (
        f'<script data-goatcounter="https://{safe}.goatcounter.com/count" '
        f'async src="//gc.zgo.at/count.js"></script>'
    )
    # The widget is hidden until the fetcher succeeds, so a flaky network or
    # disabled public stats leaves the layout clean instead of showing dashes.
    widget = (
        '<div class="visitor-stats" id="visitor-stats" hidden>'
        '<span class="vs-cell"><strong id="vs-today">—</strong> '
        '<span data-lang="en">today</span><span data-lang="bn">আজ</span></span>'
        '<span class="vs-sep">·</span>'
        '<span class="vs-cell"><strong id="vs-total">—</strong> '
        '<span data-lang="en">total</span><span data-lang="bn">মোট</span></span>'
        '</div>'
        '<script>'
        '(function(){'
        f'var GC="{safe}";'
        'var base="https://"+GC+".goatcounter.com/counter";'
        'var today=new Date().toISOString().slice(0,10);'
        'Promise.all(['
        'fetch(base+"/TOTAL.json"),'
        'fetch(base+"/TOTAL.json?start="+today+"&end="+today)'
        ']).then(function(rs){'
        'if(!rs[0].ok||!rs[1].ok)throw 0;'
        'return Promise.all(rs.map(function(r){return r.json();}));'
        '}).then(function(d){'
        'document.getElementById("vs-total").textContent='
        'd[0].count_unique||d[0].count||"—";'
        'document.getElementById("vs-today").textContent='
        'd[1].count_unique||d[1].count||"0";'
        'document.getElementById("visitor-stats").hidden=false;'
        '}).catch(function(){});'
        '})();'
        '</script>'
    )
    return head, widget


def _bilingual(en: str, bn: str, tag: str = "span", cls: str = "") -> str:
    """Render the same field in both languages.

    The CSS rule on `body[data-lang=...]` hides whichever language is not
    active. If BN is missing we emit a single tag with `data-lang="any"`
    so it stays visible in both modes (graceful degradation for the
    pre-bilingual rows already in the DB).
    """
    en_clean = (en or "").strip()
    bn_clean = (bn or "").strip()
    cls_attr = f' class="{cls}"' if cls else ""
    if not bn_clean:
        return f'<{tag}{cls_attr} data-lang="any">{_esc(en_clean)}</{tag}>'
    return (
        f'<{tag}{cls_attr} data-lang="en">{_esc(en_clean)}</{tag}>'
        f'<{tag}{cls_attr} data-lang="bn">{_esc(bn_clean)}</{tag}>'
    )


def _ui_label(key: str) -> str:
    """Render a static UI string in both languages (inline spans)."""
    en = UI["en"][key]
    bn = UI["bn"][key]
    # UI strings are already trusted (we author them); skip _esc to keep
    # entities like &rarr; intact.
    return (
        f'<span data-lang="en">{en}</span>'
        f'<span data-lang="bn">{bn}</span>'
    )


def _count_label(n: int) -> str:
    """'5 items' / '5 টি' kind of bilingual count phrase."""
    en_word = UI["en"]["item" if n == 1 else "items"]
    bn_word = UI["bn"]["item" if n == 1 else "items"]
    return (
        f'<span data-lang="en">{n} {en_word}</span>'
        f'<span data-lang="bn">{n} {bn_word}</span>'
    )


def _render_tag_pills(tags: List[str]) -> str:
    """Render the per-card tag pills. Each is clickable to toggle the filter."""
    if not tags:
        return ""
    label_map = {row["slug"]: row for row in tag_vocabulary()}
    chips = []
    for slug in tags:
        meta = label_map.get(slug)
        if not meta:
            continue
        en = _esc(meta["en"])
        bn = _esc(meta["bn"])
        chips.append(
            f'<button type="button" class="tag-pill" data-tag="{_esc(slug)}">'
            f'<span data-lang="en">{en}</span>'
            f'<span data-lang="bn">{bn}</span>'
            f'</button>'
        )
    if not chips:
        return ""
    return f'<div class="card-tags">{"".join(chips)}</div>'


def _render_card(row: Dict[str, str]) -> str:
    category = row.get("category", "")
    summary = row.get("summary", "") or ""
    summary_bn = row.get("summary_bn", "") or ""
    description = (row.get("description") or row.get("reason") or "").strip()
    description_bn = (row.get("description_bn") or row.get("reason_bn") or "").strip()
    reason = (row.get("reason") or "").strip()
    reason_bn = (row.get("reason_bn") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    date = row.get("date", "") or ""
    tags = row.get("tags") or []

    company, headline_en = _split_company(summary)
    # Bengali summary may or may not carry a ticker prefix; if BN is
    # present, use it as-is for the BN headline (the ticker is a Latin
    # token anyway and reads fine in Bangla copy).
    headline_bn = summary_bn

    company_html = (
        f'<span class="ticker" title="Company / instrument">{_esc(company)}</span>'
        if company else ""
    )
    src_html = (
        f'<a class="src" href="{_esc(source_url)}" target="_blank" '
        f'rel="noopener">{_ui_label("source")}</a>'
        if source_url else ""
    )
    tags_html = _render_tag_pills(tags)
    return (
        f'<article class="card cat-{category.lower()}">'
        f'<header>'
        f'<span class="badge">{_ui_label(category)}</span>'
        f'{company_html}'
        f'<time>{_esc(date)}</time>'
        f'</header>'
        f'{_bilingual(headline_en, headline_bn, tag="h3")}'
        f'{_bilingual(description, description_bn, tag="p", cls="desc")}'
        f'<p class="why">'
        f'<strong>{_ui_label("why")}</strong>'
        f'{_bilingual(reason, reason_bn, tag="span", cls="why-body")}'
        f'</p>'
        f'{tags_html}'
        f'{src_html}'
        f'</article>'
    )


def _render_filter_bar() -> str:
    """The filter UI rendered between stats and the groups.

    Tag chips are generated from the canonical vocabulary (so they stay
    consistent with what Gemini emits). The actual filtering happens in
    JS — see the page's <script> block.
    """
    chips = []
    for entry in tag_vocabulary():
        chips.append(
            f'<button type="button" class="filter-chip" '
            f'data-tag="{_esc(entry["slug"])}" '
            f'data-group="{_esc(entry["group"])}">'
            f'<span data-lang="en">{_esc(entry["en"])}</span>'
            f'<span data-lang="bn">{_esc(entry["bn"])}</span>'
            f'</button>'
        )
    chips_html = "".join(chips)
    return (
        '<section class="filter-bar" aria-label="Filters">'
        '  <div class="filter-row filter-row-top">'
        f'    <label class="filter-field filter-search">'
        f'      <span class="vh">{_ui_label("filters")}</span>'
        f'      <input type="search" id="f-search" autocomplete="off" '
        f'             placeholder="{UI["en"]["search_ph"]}" '
        f'             data-ph-en="{UI["en"]["search_ph"]}" '
        f'             data-ph-bn="{UI["bn"]["search_ph"]}" />'
        '    </label>'
        f'    <label class="filter-field">'
        f'      <span class="lbl">{_ui_label("date_label")}</span>'
        f'      <input type="date" id="f-date" />'
        '    </label>'
        f'    <label class="filter-field">'
        f'      <span class="vh">{_ui_label("ticker_ph")}</span>'
        f'      <input type="text" id="f-ticker" autocomplete="off" '
        f'             placeholder="{UI["en"]["ticker_ph"]}" '
        f'             data-ph-en="{UI["en"]["ticker_ph"]}" '
        f'             data-ph-bn="{UI["bn"]["ticker_ph"]}" />'
        '    </label>'
        f'    <button type="button" class="filter-reset" id="f-reset">'
        f'      {_ui_label("reset")}'
        '    </button>'
        '  </div>'
        '  <div class="filter-row filter-row-cat">'
        f'    <button type="button" class="cat-chip is-active" '
        f'            data-category="">'
        f'      {_ui_label("all")}'
        '    </button>'
        f'    <button type="button" class="cat-chip cat-good-chip" '
        f'            data-category="Good">{_ui_label("Good")}</button>'
        f'    <button type="button" class="cat-chip cat-bad-chip" '
        f'            data-category="Bad">{_ui_label("Bad")}</button>'
        f'    <button type="button" class="cat-chip cat-ugly-chip" '
        f'            data-category="Ugly">{_ui_label("Ugly")}</button>'
        '  </div>'
        '  <div class="filter-row filter-row-tags">'
        f'    <span class="filter-row-label">{_ui_label("tags_label")}</span>'
        f'    <div class="filter-chips">{chips_html}</div>'
        '  </div>'
        '  <p class="filter-status" id="f-status" hidden></p>'
        '</section>'
    )


def _render_section(category: str, rows: List[Dict[str, str]]) -> str:
    cards = "\n".join(_render_card(r) for r in rows)
    blurb_en = UI["en"]["blurbs"][category]
    blurb_bn = UI["bn"]["blurbs"][category]
    return (
        f'<section class="group group-{category.lower()}">'
        f'  <header class="group-head">'
        f'    <h2><span class="group-name">{_ui_label(category)}</span>'
        f'        <span class="count">{_count_label(len(rows))}</span></h2>'
        f'    {_bilingual(blurb_en, blurb_bn, tag="p", cls="group-blurb")}'
        f'  </header>'
        f'  <div class="cards">{cards}</div>'
        f'</section>'
    )


def write_html(items: List[Dict[str, str]], output_path: Path) -> Path:
    counts = Counter((r.get("category") or "") for r in items)
    total = sum(counts.values())

    by_cat: Dict[str, List[Dict[str, str]]] = {c: [] for c in CATEGORY_ORDER}
    for r in items:
        cat = r.get("category", "")
        if cat in by_cat:
            by_cat[cat].append(r)

    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rendered_groups = [
        _render_section(c, by_cat[c]) for c in CATEGORY_ORDER if by_cat[c]
    ]
    if rendered_groups:
        sections = '<div class="groups">' + "\n".join(rendered_groups) + "</div>"
    else:
        sections = (
            f'<p class="empty">'
            f'<span data-lang="en">{UI["en"]["empty"]}</span>'
            f'<span data-lang="bn">{UI["bn"]["empty"]}</span>'
            f'</p>'
        )

    head_title = f'{UI["en"]["title"]} | {UI["bn"]["title"]}'

    # Bilingual labels & tag metadata that the page JS needs at runtime.
    # JSON-embedded to avoid string-escaping headaches inside the template.
    js_payload = {
        "ui": {
            "en": {k: UI["en"][k] for k in (
                "Good", "Bad", "Ugly", "why", "source", "showing", "of",
                "results", "result", "no_match", "loading", "empty",
            )},
            "bn": {k: UI["bn"][k] for k in (
                "Good", "Bad", "Ugly", "why", "source", "showing", "of",
                "results", "result", "no_match", "loading", "empty",
            )},
        },
        "tag_vocabulary": tag_vocabulary(),
    }
    js_payload_json = json.dumps(js_payload, ensure_ascii=False)

    gc_head, gc_widget = _goatcounter_snippets(GOATCOUNTER_CODE)
    doc = _PAGE_TEMPLATE.format(
        head_title=_esc(head_title),
        title_en=_esc(UI["en"]["title"]),
        title_bn=_esc(UI["bn"]["title"]),
        lede_en=_esc(UI["en"]["lede"]),
        lede_bn=_esc(UI["bn"]["lede"]),
        last_updated_label=_ui_label("last_updated"),
        last_updated=_esc(last_updated),
        rss_label=_ui_label("rss"),
        total_label=_ui_label("total"),
        good_label=_ui_label("Good"),
        bad_label=_ui_label("Bad"),
        ugly_label=_ui_label("Ugly"),
        footer_gen=_ui_label("footer_gen"),
        footer_src=_ui_label("footer_src"),
        total=total,
        good=counts.get("Good", 0),
        bad=counts.get("Bad", 0),
        ugly=counts.get("Ugly", 0),
        filter_bar=_render_filter_bar(),
        sections=sections,
        goatcounter_head=gc_head,
        visitor_widget=gc_widget,
    )
    # JSON payload has literal {}s that would break str.format(); substitute
    # via sentinel after formatting. The </script> escape is a defense in
    # depth in case any field ever contains the literal string.
    safe_json = js_payload_json.replace("</", "<\\/")
    doc = doc.replace("__JS_PAYLOAD__", safe_json)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    log.info("HTML dashboard written to %s (%d items)", output_path, total)
    return output_path


# Doubled braces `{{` / `}}` are literal CSS braces inside .format(...).
_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{head_title}</title>
  <link rel="alternate" type="application/rss+xml" title="{title_en}" href="feed.xml" />
  {goatcounter_head}
  <style>
    :root {{
      --good:#16a34a; --bad:#f59e0b; --ugly:#dc2626;
      --bg:#f6f8fb; --fg:#0f172a; --card:#ffffff;
      --muted:#64748b; --line:#e2e8f0; --chip:#0f172a14;
      color-scheme: light dark;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg:#0b1220; --fg:#e6edf3; --card:#111827;
        --muted:#94a3b8; --line:#1f2937; --chip:#e6edf314;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html,body {{ margin:0; padding:0; }}
    body {{
      font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--fg);
      padding: 2rem 1.25rem 4rem;
    }}
    body[data-lang="bn"] {{
      font-family: "Noto Sans Bengali", "Hind Siliguri", "SolaimanLipi",
                   "Kalpurush", system-ui, sans-serif;
    }}

    /* Bilingual show/hide. Elements tagged data-lang="any" stay visible
       in both modes so DB rows that lack a Bengali translation don't
       disappear when the user toggles to bn. */
    body[data-lang="en"] [data-lang="bn"] {{ display: none !important; }}
    body[data-lang="bn"] [data-lang="en"] {{ display: none !important; }}

    .wrap {{ max-width: 1340px; margin: 0 auto; }}
    .top {{
      display:flex; align-items:flex-start; justify-content:space-between;
      flex-wrap:wrap; gap:1rem;
    }}
    h1 {{ margin:0; font-size:1.8rem; letter-spacing:-.02em; }}
    .sub {{ color: var(--muted); margin: .25rem 0 0; }}
    .top-actions {{ display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }}

    .lang-switch {{
      display:inline-flex; border:1px solid var(--line); border-radius:999px;
      overflow:hidden; background: var(--card);
    }}
    .lang-switch button {{
      background: transparent; color: var(--fg); border:0;
      padding:.4rem .8rem; font: inherit; font-size:.82rem;
      cursor:pointer; line-height:1;
    }}
    body[data-lang="en"] .lang-switch [data-set-lang="en"],
    body[data-lang="bn"] .lang-switch [data-set-lang="bn"] {{
      background: var(--fg); color: var(--bg); font-weight:700;
    }}

    .rss {{
      font-size:.85rem; padding:.4rem .85rem; border-radius:999px;
      border:1px solid var(--line); text-decoration:none; color:var(--fg);
      background: var(--card);
    }}
    .visitor-stats {{
      display:inline-flex; align-items:center; gap:.4rem;
      font-size:.78rem; color: var(--muted);
      padding:.4rem .8rem; border-radius:999px;
      border:1px solid var(--line); background: var(--card);
      font-variant-numeric: tabular-nums; line-height:1;
    }}
    .visitor-stats strong {{ color: var(--fg); font-weight:700; }}
    .visitor-stats .vs-sep {{ opacity:.45; }}
    .lede {{ margin: 1.25rem 0 0; max-width: 60ch; color: var(--fg); }}

    .stats {{
      display:grid; gap:1rem;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      margin: 2rem 0 2.5rem;
    }}
    .stat {{
      background: var(--card); border:1px solid var(--line);
      border-radius:14px; padding:1.1rem 1.25rem;
      box-shadow: 0 1px 0 rgba(15,23,42,.02);
    }}
    .stat .num {{
      font-size: 2.6rem; font-weight: 800; line-height:1;
      letter-spacing:-.03em; font-variant-numeric: tabular-nums;
    }}
    .stat .label {{
      margin-top:.4rem; font-size:.78rem; color: var(--muted);
      text-transform: uppercase; letter-spacing:.08em; font-weight:600;
    }}
    .stat.good .num, .stat.good .label {{ color: var(--good); }}
    .stat.bad  .num, .stat.bad  .label {{ color: var(--bad);  }}
    .stat.ugly .num, .stat.ugly .label {{ color: var(--ugly); }}

    .groups {{
      display:grid; gap:1.25rem; align-items:start;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-top: 1.5rem;
    }}
    @media (max-width: 1100px) {{
      .groups {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 720px) {{
      .groups {{ grid-template-columns: 1fr; }}
    }}
    .group {{
      background: var(--card); border:1px solid var(--line);
      border-radius:14px; padding:.85rem .9rem 1rem;
      display:flex; flex-direction:column; min-width: 0;
    }}
    .group-head {{
      display:flex; align-items:baseline; gap:.55rem; flex-wrap:wrap;
      padding-bottom:.55rem; margin-bottom:.85rem;
      border-bottom: 2px solid var(--line);
    }}
    .group-good .group-head {{ border-bottom-color: var(--good); }}
    .group-bad  .group-head {{ border-bottom-color: var(--bad);  }}
    .group-ugly .group-head {{ border-bottom-color: var(--ugly); }}
    .group-head h2 {{
      margin:0; display:flex; align-items:baseline; gap:.5rem;
      font-size:1rem; font-weight:600;
    }}
    .group-name {{
      display:inline-block; font-size:1.15rem; font-weight:800;
      padding:.18rem .7rem; border-radius:8px; color:#fff;
      letter-spacing:.04em; text-transform:uppercase;
      box-shadow: 0 2px 6px rgba(15,23,42,.15);
    }}
    .group-good .group-name {{ background: var(--good); }}
    .group-bad  .group-name {{ background: var(--bad);  }}
    .group-ugly .group-name {{ background: var(--ugly); }}
    .count {{
      color: var(--muted); font-weight:600; font-size:.75rem;
      padding:.12rem .45rem; border-radius:999px; background: var(--chip);
    }}
    .group-blurb {{
      color: var(--muted); margin:0; font-size:.78rem; flex:1 1 100%;
    }}

    .cards {{ display:flex; flex-direction:column; gap:.7rem; }}
    .card {{
      background: var(--bg); border:1px solid var(--line);
      border-left:3px solid var(--line);
      border-radius:10px; padding:.7rem .85rem;
      display:flex; flex-direction:column;
    }}
    @media (prefers-color-scheme: dark) {{
      .card {{ background: rgba(255,255,255,.025); }}
    }}
    .cat-good {{ border-left-color: var(--good); }}
    .cat-bad  {{ border-left-color: var(--bad);  }}
    .cat-ugly {{ border-left-color: var(--ugly); }}
    .card header {{
      display:flex; align-items:center; gap:.4rem; flex-wrap:wrap;
      margin-bottom:.35rem;
    }}
    .badge {{
      font-size:.62rem; font-weight:700; padding:.12rem .45rem;
      border-radius:999px; text-transform:uppercase; letter-spacing:.06em;
      color:#fff;
    }}
    .cat-good .badge {{ background: var(--good); }}
    .cat-bad  .badge {{ background: var(--bad);  }}
    .cat-ugly .badge {{ background: var(--ugly); }}
    .ticker {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-weight:700; font-size:.74rem;
      padding:.1rem .4rem; border-radius:5px; background: var(--chip);
    }}
    .card time {{
      margin-left:auto; color: var(--muted); font-size:.72rem;
      font-variant-numeric: tabular-nums;
    }}
    .card h3 {{
      margin:.1rem 0 .4rem; font-size:.95rem; line-height:1.3;
      font-weight:600;
    }}
    .desc {{ margin: 0 0 .5rem; font-size:.86rem; line-height:1.45; }}
    .why {{
      margin: 0 0 .45rem; padding:.5rem .65rem;
      background: var(--chip); border-radius:6px;
      font-size:.8rem; line-height:1.45;
    }}
    .why strong {{
      font-size:.68rem; text-transform:uppercase; letter-spacing:.06em;
      display:block; color: var(--muted); margin-bottom:.15rem;
      font-weight:700;
    }}
    .src {{
      margin-top:auto; font-size:.78rem; color:var(--fg);
      text-decoration:none; border-bottom:1px dashed var(--muted);
      align-self:flex-start;
    }}
    .src:hover {{ border-bottom-style:solid; }}

    /* Per-card tag pills (shown at bottom of each card). */
    .card-tags {{
      display:flex; flex-wrap:wrap; gap:.3rem;
      margin: 0 0 .5rem;
    }}
    .tag-pill {{
      font: inherit; font-size:.7rem; line-height:1;
      padding:.22rem .5rem; border-radius:999px; cursor:pointer;
      background: var(--chip); color: var(--fg);
      border:1px solid var(--line); white-space:nowrap;
    }}
    .tag-pill:hover {{ border-color: var(--fg); }}

    /* Filter bar above the groups. */
    .filter-bar {{
      background: var(--card); border:1px solid var(--line);
      border-radius:14px; padding: 1rem 1.1rem;
      margin: 0 0 1.5rem;
      display:flex; flex-direction:column; gap:.7rem;
    }}
    .filter-row {{ display:flex; flex-wrap:wrap; gap:.55rem; align-items:center; }}
    .filter-row-top {{ gap:.55rem; }}
    .filter-row-label {{
      font-size:.72rem; color: var(--muted); font-weight:700;
      text-transform:uppercase; letter-spacing:.08em;
      margin-right:.2rem;
    }}
    .filter-field {{
      display:flex; flex-direction:column; gap:.15rem; flex:0 0 auto;
    }}
    .filter-field.filter-search {{ flex: 1 1 220px; min-width: 180px; }}
    .filter-field .lbl {{
      font-size:.65rem; color: var(--muted); font-weight:600;
      text-transform:uppercase; letter-spacing:.07em;
    }}
    .vh {{
      position:absolute !important; width:1px; height:1px;
      padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0);
      white-space:nowrap; border:0;
    }}
    .filter-bar input[type="search"],
    .filter-bar input[type="text"],
    .filter-bar input[type="date"] {{
      font: inherit; font-size:.88rem;
      padding:.42rem .6rem; border-radius:8px;
      border:1px solid var(--line); background: var(--bg); color: var(--fg);
      min-width: 9rem;
    }}
    .filter-bar input:focus {{ outline: 2px solid var(--fg); outline-offset: 1px; }}
    .filter-reset {{
      font: inherit; font-size:.78rem; font-weight:600;
      padding:.45rem .8rem; border-radius:8px; cursor:pointer;
      background: transparent; color: var(--muted);
      border:1px solid var(--line); margin-left:auto;
    }}
    .filter-reset:hover {{ color: var(--fg); border-color: var(--fg); }}

    .cat-chip, .filter-chip {{
      font: inherit; font-size:.75rem; font-weight:600;
      padding:.3rem .7rem; border-radius:999px; cursor:pointer;
      background: transparent; color: var(--fg);
      border:1px solid var(--line); line-height:1;
    }}
    .cat-chip:hover, .filter-chip:hover {{ border-color: var(--fg); }}
    .cat-chip.is-active {{
      background: var(--fg); color: var(--bg); border-color: var(--fg);
    }}
    .cat-chip.cat-good-chip.is-active  {{ background: var(--good); border-color: var(--good); color:#fff; }}
    .cat-chip.cat-bad-chip.is-active   {{ background: var(--bad);  border-color: var(--bad);  color:#fff; }}
    .cat-chip.cat-ugly-chip.is-active  {{ background: var(--ugly); border-color: var(--ugly); color:#fff; }}
    .filter-chip.is-active {{
      background: var(--fg); color: var(--bg); border-color: var(--fg);
    }}
    .filter-chips {{ display:flex; flex-wrap:wrap; gap:.35rem; }}
    .filter-status {{
      margin:.2rem 0 0; font-size:.78rem; color: var(--muted);
    }}

    /* Filtered (JS-rendered) view: flat 3-column grid. */
    #filtered-view {{
      display:grid; gap: .8rem;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-top: 0;
    }}
    @media (max-width: 1100px) {{
      #filtered-view {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 720px) {{
      #filtered-view {{ grid-template-columns: 1fr; }}
    }}
    #filtered-view .card {{ background: var(--card); }}
    #filtered-empty {{
      grid-column: 1 / -1;
      background: var(--card); border:1px dashed var(--line);
      padding:2rem; border-radius:12px; text-align:center;
      color: var(--muted);
    }}

    .empty {{
      background: var(--card); border:1px dashed var(--line);
      padding:2rem; border-radius:12px; text-align:center;
      color: var(--muted);
    }}
    footer.foot {{
      text-align:center; color: var(--muted);
      margin-top:3rem; font-size:.85rem;
    }}
    footer.foot a {{ color: var(--muted); }}
  </style>
</head>
<body data-lang="en">
  <div class="wrap">
    <div class="top">
      <div>
        <h1><span data-lang="en">{title_en}</span><span data-lang="bn">{title_bn}</span></h1>
        <p class="sub">{last_updated_label} {last_updated}</p>
      </div>
      <div class="top-actions">
        <div class="lang-switch" role="group" aria-label="Language">
          <button type="button" data-set-lang="en">EN</button>
          <button type="button" data-set-lang="bn">বাং</button>
        </div>
        <a class="rss" href="feed.xml">{rss_label}</a>
        {visitor_widget}
      </div>
    </div>
    <p class="lede"><span data-lang="en">{lede_en}</span><span data-lang="bn">{lede_bn}</span></p>

    <div class="stats">
      <div class="stat total"><div class="num">{total}</div><div class="label">{total_label}</div></div>
      <div class="stat good"><div class="num">{good}</div><div class="label">{good_label}</div></div>
      <div class="stat bad"><div class="num">{bad}</div><div class="label">{bad_label}</div></div>
      <div class="stat ugly"><div class="num">{ugly}</div><div class="label">{ugly_label}</div></div>
    </div>

    {filter_bar}

    <div id="default-view">
      {sections}
    </div>
    <div id="filtered-view" hidden></div>

    <footer class="foot">
      {footer_gen} &middot; <a href="feed.xml">RSS</a> &middot;
      <a href="https://github.com/alaminmain/BdCapitalMarketNews">{footer_src}</a>
    </footer>
  </div>
  <script type="application/json" id="ui-payload">__JS_PAYLOAD__</script>
  <script>
    (function() {{
      var payload = JSON.parse(document.getElementById('ui-payload').textContent);
      var UI = payload.ui;
      var TAG_VOCAB = payload.tag_vocabulary;
      var TAG_LABELS = {{}};
      TAG_VOCAB.forEach(function(t) {{ TAG_LABELS[t.slug] = t; }});

      // --- Language switching ---
      var LANG_KEY = 'bd-pulse-lang';
      var saved = null;
      try {{ saved = localStorage.getItem(LANG_KEY); }} catch (e) {{}}
      var initialLang = saved
        || ((navigator.language || '').toLowerCase().indexOf('bn') === 0 ? 'bn' : 'en');
      setLang(initialLang);

      function setLang(l) {{
        document.body.dataset.lang = l;
        document.documentElement.lang = l;
        // Swap placeholders on filter inputs to match the active language.
        document.querySelectorAll('input[data-ph-en]').forEach(function(el) {{
          el.placeholder = el.getAttribute('data-ph-' + l) || el.placeholder;
        }});
      }}

      // --- Filter state ---
      var state = {{
        q: '', date: '', category: '', tags: new Set(), ticker: ''
      }};
      var historyCache = null;
      var historyLoading = null;

      var $search = document.getElementById('f-search');
      var $date = document.getElementById('f-date');
      var $ticker = document.getElementById('f-ticker');
      var $status = document.getElementById('f-status');
      var $defaultView = document.getElementById('default-view');
      var $filteredView = document.getElementById('filtered-view');

      function isActive() {{
        return !!(state.q || state.date || state.category
                  || state.tags.size || state.ticker);
      }}

      function ensureHistory() {{
        if (historyCache) return Promise.resolve(historyCache);
        if (historyLoading) return historyLoading;
        historyLoading = fetch('history.json', {{ cache: 'no-cache' }})
          .then(function(r) {{
            if (!r.ok) throw new Error('history.json HTTP ' + r.status);
            return r.json();
          }})
          .then(function(j) {{ historyCache = j; return j; }})
          .catch(function(err) {{ historyLoading = null; throw err; }});
        return historyLoading;
      }}

      function setStatus(text) {{
        if (!text) {{ $status.hidden = true; $status.textContent = ''; }}
        else {{ $status.hidden = false; $status.textContent = text; }}
      }}

      function escapeHtml(s) {{
        return String(s == null ? '' : s)
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      }}

      function splitCompany(summary) {{
        var m = (summary || '').match(/^([A-Z0-9][A-Z0-9 .&-]{{1,18}}):\\s*(.+)$/);
        if (!m) return {{ ticker: '', rest: summary || '' }};
        return {{ ticker: m[1].trim(), rest: m[2].trim() }};
      }}

      function bilingual(en, bn, tag, cls) {{
        tag = tag || 'span';
        var c = cls ? ' class="' + cls + '"' : '';
        if (!bn) return '<' + tag + c + ' data-lang="any">' + escapeHtml(en) + '</' + tag + '>';
        return '<' + tag + c + ' data-lang="en">' + escapeHtml(en) + '</' + tag + '>'
             + '<' + tag + c + ' data-lang="bn">' + escapeHtml(bn) + '</' + tag + '>';
      }}

      function uiBoth(key) {{
        // UI strings author-controlled so we don't escape (preserves &rarr; etc).
        return '<span data-lang="en">' + UI.en[key] + '</span>'
             + '<span data-lang="bn">' + UI.bn[key] + '</span>';
      }}

      function renderTagPills(tags) {{
        if (!tags || !tags.length) return '';
        var pills = tags.map(function(slug) {{
          var meta = TAG_LABELS[slug];
          if (!meta) return '';
          return '<button type="button" class="tag-pill" data-tag="'
            + escapeHtml(slug) + '">'
            + '<span data-lang="en">' + escapeHtml(meta.en) + '</span>'
            + '<span data-lang="bn">' + escapeHtml(meta.bn) + '</span>'
            + '</button>';
        }}).join('');
        return pills ? '<div class="card-tags">' + pills + '</div>' : '';
      }}

      function renderCard(r) {{
        var cat = r.category || '';
        var split = splitCompany(r.summary || '');
        var company = split.ticker || r.ticker || '';
        var headlineEn = split.rest;
        var headlineBn = r.summary_bn || '';
        var desc = r.description || r.reason || '';
        var descBn = r.description_bn || r.reason_bn || '';
        var reason = r.reason || '';
        var reasonBn = r.reason_bn || '';
        var source = r.source_url || '';
        var date = r.date || '';

        var srcHtml = source
          ? '<a class="src" href="' + escapeHtml(source)
            + '" target="_blank" rel="noopener">'
            + '<span data-lang="en">' + UI.en.source + '</span>'
            + '<span data-lang="bn">' + UI.bn.source + '</span></a>'
          : '';

        return '<article class="card cat-' + cat.toLowerCase() + '">'
          + '<header>'
          +   '<span class="badge">' + uiBoth(cat) + '</span>'
          +   (company ? '<span class="ticker">' + escapeHtml(company) + '</span>' : '')
          +   '<time>' + escapeHtml(date) + '</time>'
          + '</header>'
          + bilingual(headlineEn, headlineBn, 'h3')
          + bilingual(desc, descBn, 'p', 'desc')
          + '<p class="why"><strong>' + uiBoth('why') + '</strong>'
          +   bilingual(reason, reasonBn, 'span', 'why-body')
          + '</p>'
          + renderTagPills(r.tags)
          + srcHtml
          + '</article>';
      }}

      function rowMatches(r) {{
        if (state.category && r.category !== state.category) return false;
        if (state.date && r.date !== state.date) return false;
        if (state.ticker) {{
          var tk = (r.ticker || '').toLowerCase();
          if (tk.indexOf(state.ticker.toLowerCase()) === -1) return false;
        }}
        if (state.tags.size) {{
          var hit = false;
          for (var i = 0; i < r.tags.length; i++) {{
            if (state.tags.has(r.tags[i])) {{ hit = true; break; }}
          }}
          if (!hit) return false;
        }}
        if (state.q) {{
          var lang = document.body.dataset.lang || 'en';
          var bag = lang === 'bn'
            ? [r.summary_bn, r.description_bn, r.reason_bn, r.ticker, r.summary]
            : [r.summary, r.description, r.reason, r.ticker, r.summary_bn];
          if (bag.join(' ').toLowerCase().indexOf(state.q.toLowerCase()) === -1)
            return false;
        }}
        return true;
      }}

      function apply() {{
        if (!isActive()) {{
          $defaultView.hidden = false;
          $filteredView.hidden = true;
          $filteredView.innerHTML = '';
          setStatus('');
          return;
        }}
        $defaultView.hidden = true;
        $filteredView.hidden = false;
        var lang = document.body.dataset.lang || 'en';
        setStatus(UI[lang].loading);
        ensureHistory().then(function(h) {{
          var rows = h.rows.filter(rowMatches);
          renderResults(rows, h.rows.length, lang);
        }}).catch(function(err) {{
          $filteredView.innerHTML =
            '<p id="filtered-empty">' + escapeHtml(String(err.message || err)) + '</p>';
          setStatus('');
        }});
      }}

      function renderResults(rows, total, lang) {{
        var t = UI[lang];
        if (!rows.length) {{
          $filteredView.innerHTML =
            '<p id="filtered-empty">' + escapeHtml(t.no_match) + '</p>';
          setStatus('');
          return;
        }}
        $filteredView.innerHTML = rows.map(renderCard).join('');
        var noun = rows.length === 1 ? t.result : t.results;
        setStatus(t.showing + ' ' + rows.length + ' ' + t.of + ' ' + total
                  + ' ' + noun);
      }}

      function syncTagChips() {{
        document.querySelectorAll('.filter-chip').forEach(function(el) {{
          el.classList.toggle('is-active', state.tags.has(el.dataset.tag));
        }});
      }}

      function syncCatChips() {{
        document.querySelectorAll('.cat-chip').forEach(function(el) {{
          el.classList.toggle('is-active',
            (el.dataset.category || '') === state.category);
        }});
      }}

      function toggleTag(slug) {{
        if (!slug) return;
        if (state.tags.has(slug)) state.tags.delete(slug);
        else state.tags.add(slug);
        syncTagChips();
        apply();
      }}

      function setCategory(cat) {{
        state.category = cat || '';
        syncCatChips();
        apply();
      }}

      function resetAll() {{
        state.q = ''; state.date = ''; state.category = '';
        state.ticker = ''; state.tags.clear();
        $search.value = ''; $date.value = ''; $ticker.value = '';
        syncTagChips(); syncCatChips();
        apply();
      }}

      function debounce(fn, ms) {{
        var t;
        return function() {{
          clearTimeout(t);
          var args = arguments, ctx = this;
          t = setTimeout(function() {{ fn.apply(ctx, args); }}, ms);
        }};
      }}

      // --- Event wiring ---
      document.addEventListener('click', function(e) {{
        var langBtn = e.target.closest && e.target.closest('[data-set-lang]');
        if (langBtn) {{
          var l = langBtn.dataset.setLang;
          setLang(l);
          try {{ localStorage.setItem(LANG_KEY, l); }} catch (e) {{}}
          if (isActive()) apply();
          return;
        }}
        var pill = e.target.closest && e.target.closest('.tag-pill');
        if (pill) {{ toggleTag(pill.dataset.tag); return; }}
        var chip = e.target.closest && e.target.closest('.filter-chip');
        if (chip) {{ toggleTag(chip.dataset.tag); return; }}
        var catChip = e.target.closest && e.target.closest('.cat-chip');
        if (catChip) {{ setCategory(catChip.dataset.category); return; }}
        if (e.target && e.target.id === 'f-reset') {{ resetAll(); return; }}
      }});

      $search.addEventListener('input', debounce(function(e) {{
        state.q = (e.target.value || '').trim();
        apply();
      }}, 120));
      $date.addEventListener('change', function(e) {{
        state.date = e.target.value || '';
        apply();
      }});
      $ticker.addEventListener('input', debounce(function(e) {{
        state.ticker = (e.target.value || '').trim();
        apply();
      }}, 120));
    }})();
  </script>
</body>
</html>
"""
