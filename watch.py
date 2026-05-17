"""Price watcher for ro.gnjoylatam.com shop-search. See README for details."""

import datetime
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state.json"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
BASE_URL = "https://ro.gnjoylatam.com/pt/intro/shop-search/trading"
SLEEP_BETWEEN_ITEMS = 2.0


def build_url(server: str, search: str) -> str:
    qs = urllib.parse.urlencode(
        {
            "storeType": "BUY",
            "serverType": server,
            "searchWord": search,
            "sortType": "LOW_PRICE",
        }
    )
    return f"{BASE_URL}?{qs}"


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.gnjoylatam.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


CARD_RE = re.compile(
    r'<li class="card_shop_card__[^"]*[^>]*'
    r'data-id="(?P<id>\d+)"[^>]*'
    r'data-ssi="(?P<ssi>\d+)"'
    r'.*?card_item_price__[^"]*"><span[^>]*>(?P<price>[\d.]+)</span>'
    r'.*?<span>Nome do Com.{0,3}rcio</span>'
    r'<span class="card_shop_info_name__[^"]*">(?P<trade>[^<]*)</span>'
    r'.*?<span>Vendedor</span>'
    r'<span class="card_shop_info_name__[^"]*">(?P<seller>[^<]*)</span>',
    re.DOTALL,
)


def parse_listings(html_text: str, item_id: int) -> list[dict]:
    """Return all listings matching item_id, sorted ascending by price."""
    listings = []
    for m in CARD_RE.finditer(html_text):
        if int(m.group("id")) != item_id:
            continue
        price_int = int(m.group("price").replace(".", ""))
        listings.append(
            {
                "ssi": m.group("ssi"),
                "price": price_int,
                "trade": html.unescape(m.group("trade")).strip(),
                "seller": html.unescape(m.group("seller")).strip(),
            }
        )
    listings.sort(key=lambda x: x["price"])
    return listings


def notify(topic: str, title: str, body: str, click_url: str) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Tags": "moneybag",
            "Priority": "high",
            "Click": click_url,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARN: {STATE_PATH} is corrupt, starting fresh", file=sys.stderr)
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def md_escape(s: str) -> str:
    """Escape characters that break markdown table cells."""
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip() or "—"


# Status codes used by process_item to drive log lines, summary rendering, and exit code.
STATUS_OK = "ok"            # listings present, lowest above target
STATUS_EMPTY = "empty"      # zero listings for this item id
STATUS_ERROR = "error"      # fetch/parse error
STATUS_NOTIFIED = "notified"    # lowest <= target AND a notification was sent this run
STATUS_SILENCED = "silenced"    # lowest <= target but already alerted at this price (no notif)
STATUS_DRY_RUN = "dry_run"      # lowest <= target but NTFY_TOPIC missing


def process_item(item: dict, server: str, topic: str | None, state: dict) -> dict:
    """Returns a result dict for log output, summary rendering, and exit-code accounting."""
    name = item["name"]
    item_id = int(item["item_id"])
    target = int(item["target_price"])
    url = build_url(server, item["search"])

    result = {
        "name": name,
        "item_id": item_id,
        "target": target,
        "url": url,
        "listings": [],
        "status": STATUS_OK,
        "error": None,
    }

    try:
        page_html = fetch(url)
    except Exception as e:
        print(f"[{name}] ERROR fetching: {e}", file=sys.stderr)
        result["status"] = STATUS_ERROR
        result["error"] = str(e)
        return result

    listings = parse_listings(page_html, item_id)
    result["listings"] = listings
    state_key = str(item_id)
    item_state = state.get(state_key, {"last_alerted_price": None})

    if not listings:
        print(f"[{name}] 0 listings (target <= {fmt(target)}z)")
        result["status"] = STATUS_EMPTY
        item_state["last_alerted_price"] = None
        state[state_key] = item_state
        return result

    lowest = listings[0]
    print(
        f"[{name}] {len(listings)} listings, "
        f"lowest={fmt(lowest['price'])}z "
        f"(seller={lowest['seller']!r}, trade={lowest['trade']!r}) "
        f"target<={fmt(target)}z"
    )

    last_alerted = item_state.get("last_alerted_price")

    if lowest["price"] > target:
        if last_alerted is not None:
            print("  reset: lowest is above target, clearing last_alerted")
        item_state["last_alerted_price"] = None
        state[state_key] = item_state
        result["status"] = STATUS_OK
        return result

    should_notify = last_alerted is None or lowest["price"] < last_alerted

    if not should_notify:
        print(
            f"  skip notify: lowest {fmt(lowest['price'])}z not lower than "
            f"last alerted {fmt(last_alerted)}z"
        )
        state[state_key] = item_state
        result["status"] = STATUS_SILENCED
        return result

    if topic is None:
        print("  WARN: NTFY_TOPIC not set, would have notified but skipping", file=sys.stderr)
        state[state_key] = item_state
        result["status"] = STATUS_DRY_RUN
        return result

    title = f"{name} @ {fmt(lowest['price'])}z"
    body = (
        f"Menor preço: {fmt(lowest['price'])}z (alvo <= {fmt(target)}z). "
        f"Vendedor: {lowest['seller']}. Comércio: {lowest['trade']}."
    )
    try:
        notify(topic, title, body, url)
        print(f"  NOTIFIED: {title}")
        item_state["last_alerted_price"] = lowest["price"]
        result["status"] = STATUS_NOTIFIED
    except Exception as e:
        print(f"  ERROR sending notification: {e}", file=sys.stderr)
        result["status"] = STATUS_ERROR
        result["error"] = f"notification failed: {e}"

    state[state_key] = item_state
    return result


STATUS_BADGE = {
    STATUS_OK: ("✅", "acima do alvo"),
    STATUS_EMPTY: ("⚪", "sem listagens"),
    STATUS_ERROR: ("❌", "erro"),
    STATUS_NOTIFIED: ("🎯", "ABAIXO DO ALVO — notificação enviada"),
    STATUS_SILENCED: ("🔇", "abaixo do alvo — silenciado (já alertado nesse preço)"),
    STATUS_DRY_RUN: ("⚠️", "abaixo do alvo — `NTFY_TOPIC` não configurado"),
}


def render_summary(results: list[dict], server: str, ran_at: datetime.datetime) -> str:
    lines = []
    notified_count = sum(1 for r in results if r["status"] == STATUS_NOTIFIED)
    lines.append("# Resultado da execução")
    lines.append("")
    lines.append(
        f"_Servidor: **{server}** · "
        f"Executado em {ran_at.strftime('%Y-%m-%d %H:%M:%S UTC')}_"
    )
    lines.append("")

    if notified_count > 0:
        lines.append(f"🎯 **{notified_count} notificação(ões) enviada(s) nesta execução.**")
    else:
        lines.append("Nenhuma notificação enviada nesta execução.")
    lines.append("")

    # Per-item sections
    for r in results:
        emoji, status_label = STATUS_BADGE.get(r["status"], ("•", r["status"]))
        lines.append(f"## {emoji} {r['name']}")
        lines.append("")
        lines.append(
            f"**Alvo:** ≤ {fmt(r['target'])}z &nbsp;·&nbsp; "
            f"**Listagens:** {len(r['listings'])} &nbsp;·&nbsp; "
            f"**Status:** {status_label}"
        )
        lines.append("")
        lines.append(f"[Ver no site]({r['url']})")
        lines.append("")

        if r["status"] == STATUS_ERROR:
            lines.append(f"> ❌ Erro: `{r['error']}`")
            lines.append("")
            continue

        if not r["listings"]:
            lines.append("> Nenhuma listagem encontrada para esse `item_id` no servidor.")
            lines.append("")
            continue

        lines.append("| # | Preço | Vendedor | Comércio |")
        lines.append("|---|------:|----------|----------|")
        for idx, lst in enumerate(r["listings"], start=1):
            price_str = f"{fmt(lst['price'])}z"
            # Bold the lowest row, plus add a marker if it's at or below target.
            if idx == 1:
                marker = " 🎯" if lst["price"] <= r["target"] else ""
                price_str = f"**{price_str}**{marker}"
            lines.append(
                f"| {idx} | {price_str} | "
                f"{md_escape(lst['seller'])} | {md_escape(lst['trade'])} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_step_summary(content: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        print(f"WARN: could not write step summary: {e}", file=sys.stderr)


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found", file=sys.stderr)
        return 1

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    server = config.get("server", "FREYA")
    items = config.get("items") or []

    if not items:
        print("ERROR: config has no items", file=sys.stderr)
        return 1

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("WARN: NTFY_TOPIC not set — running in dry-run mode (no notifications)", file=sys.stderr)

    state = load_state()
    results = []

    for idx, item in enumerate(items):
        if idx > 0:
            time.sleep(SLEEP_BETWEEN_ITEMS)
        results.append(process_item(item, server, topic, state))

    save_state(state)

    ran_at = datetime.datetime.now(datetime.timezone.utc)
    write_step_summary(render_summary(results, server, ran_at))

    successes = sum(1 for r in results if r["status"] != STATUS_ERROR)
    if successes == 0:
        print("ERROR: every item failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
