"""Render the GitHub Pages dashboard (index.html) from market updates."""
from __future__ import annotations

import html
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .config import FEED_DESCRIPTION, FEED_TITLE

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
        "Good":         "Good",
        "Bad":          "Bad",
        "Ugly":         "Ugly",
        "items":        "items",
        "item":         "item",
        "why":          "Why this matters",
        "source":       "Read source &rarr;",
        "footer_gen":   "Generated automatically",
        "footer_src":   "Source on GitHub",
        "empty":        "No classified items yet — check back soon.",
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
        "Good":         "ভালো",
        "Bad":          "খারাপ",
        "Ugly":         "ভয়াবহ",
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
        f'{src_html}'
        f'</article>'
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
        sections=sections,
    )

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
      </div>
    </div>
    <p class="lede"><span data-lang="en">{lede_en}</span><span data-lang="bn">{lede_bn}</span></p>

    <div class="stats">
      <div class="stat total"><div class="num">{total}</div><div class="label">{total_label}</div></div>
      <div class="stat good"><div class="num">{good}</div><div class="label">{good_label}</div></div>
      <div class="stat bad"><div class="num">{bad}</div><div class="label">{bad_label}</div></div>
      <div class="stat ugly"><div class="num">{ugly}</div><div class="label">{ugly_label}</div></div>
    </div>

    {sections}

    <footer class="foot">
      {footer_gen} &middot; <a href="feed.xml">RSS</a> &middot;
      <a href="https://github.com/alaminmain/BdCapitalMarketNews">{footer_src}</a>
    </footer>
  </div>
  <script>
    (function() {{
      var KEY = 'bd-pulse-lang';
      var saved = null;
      try {{ saved = localStorage.getItem(KEY); }} catch (e) {{}}
      var initial = saved
        || ((navigator.language || '').toLowerCase().indexOf('bn') === 0 ? 'bn' : 'en');
      document.body.dataset.lang = initial;
      document.documentElement.lang = initial;
      document.addEventListener('click', function(e) {{
        var btn = e.target.closest && e.target.closest('[data-set-lang]');
        if (!btn) return;
        var l = btn.dataset.setLang;
        document.body.dataset.lang = l;
        document.documentElement.lang = l;
        try {{ localStorage.setItem(KEY, l); }} catch (e) {{}}
      }});
    }})();
  </script>
</body>
</html>
"""
