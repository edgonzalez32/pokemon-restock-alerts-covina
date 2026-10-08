#!/usr/bin/env python3
"""Pokemon TCG 30th Celebration restock checker.

Watches Target online and specific Target stores for the products in
config.json, and sends a push notification (via ntfy.sh) the moment one
goes from unavailable to available. Also sends timed reminders for the
usual in-store restock windows and release days.

Standard library only, so it runs anywhere Python 3.10+ does.

Environment:
  NTFY_TOPIC      ntfy.sh topic to publish to (no topic = print only)
  NTFY_SERVER     defaults to https://ntfy.sh
  LOOP_SECONDS    keep checking for this many seconds (default 0 = one pass)
  INTERVAL        seconds between passes when looping (default 60)
  STATE_PATH      where state is read/written (default state.json)
  STATUS_PATH     dashboard status file (default status.json)
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

REDSKY = "https://redsky.target.com/redsky_aggregations/v1/web"
# Target's public web key, the same one target.com sends from every browser.
REDSKY_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
AVAILABLE = {"IN_STOCK", "LIMITED_STOCK", "PRE_ORDER_SELLABLE", "AVAILABLE"}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MAX_EVENTS = 60
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------- helpers

def dig(obj, *path, default=None):
    for key in path:
        if isinstance(obj, dict) and key in obj:
            obj = obj[key]
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return default
    return obj


def http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": "https://www.target.com",
        "Referer": "https://www.target.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def product_url(tcin: str) -> str:
    return f"https://www.target.com/p/-/A-{tcin}"


def load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ----------------------------------------------------------- notification

class Notifier:
    def __init__(self, topic: str | None, server: str = "https://ntfy.sh",
                 push_subs: list[dict] | None = None, vapid_key: str | None = None):
        self.topic = topic
        self.server = server.rstrip("/")
        self.push_subs = push_subs or []
        self.vapid_key = vapid_key
        self.sent: list[dict] = []

    def web_push(self, title: str, message: str, url: str | None, priority: int) -> None:
        if not (self.push_subs and self.vapid_key):
            return
        import webpush  # needs `cryptography`; only loaded when the app is set up
        msg = {"title": title, "body": message, "url": url, "urgent": priority >= 5}
        for sub in self.push_subs:
            try:
                status = webpush.send(sub, msg, self.vapid_key)
                if status >= 300:
                    print(f"  ! app push returned HTTP {status}", file=sys.stderr)
            except Exception as exc:
                print(f"  ! app push failed: {exc}", file=sys.stderr)

    def send(self, title: str, message: str, url: str | None = None,
             priority: int = 3, tags: list[str] | None = None) -> None:
        payload = {"topic": self.topic, "title": title, "message": message,
                   "priority": priority, "tags": tags or []}
        if url:
            payload["click"] = url
            payload["actions"] = [{"action": "view", "label": "Open", "url": url}]
        self.sent.append(payload)
        print(f"[notify p{priority}] {title}: {message} {url or ''}", flush=True)
        self.web_push(title, message, url, priority)
        if not self.topic:
            return
        req = urllib.request.Request(
            self.server, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as exc:  # never let a notification failure stop checks
            print(f"  ! ntfy publish failed: {exc}", file=sys.stderr)


# ------------------------------------------------------------ Target API

def parse_summaries(data: dict, store_id: str) -> dict[str, dict]:
    """Turn a product_summary_with_fulfillment_v1 response into
    {tcin: {"title", "online", "store", "store_qty"}}."""
    out = {}
    for s in dig(data, "data", "product_summaries", default=[]) or []:
        tcin = str(s.get("tcin", ""))
        if not tcin:
            continue
        ful = s.get("fulfillment") or {}
        online = dig(ful, "shipping_options", "availability_status", default="UNKNOWN")
        store_status, qty = "UNKNOWN", 0
        for opt in ful.get("store_options") or []:
            if str(opt.get("location_id")) != str(store_id):
                continue
            qty = int(opt.get("location_available_to_promise_quantity") or 0)
            pickup = dig(opt, "order_pickup", "availability_status", default="")
            instore = dig(opt, "in_store_only", "availability_status", default="")
            if pickup in AVAILABLE or instore in AVAILABLE or qty > 0:
                store_status = "IN_STOCK"
            else:
                store_status = pickup or instore or "OUT_OF_STOCK"
        out[tcin] = {
            "title": dig(s, "item", "product_description", "title", default=""),
            "online": online,
            "store": store_status,
            "store_qty": qty,
        }
    return out


def fetch_target(cfg: dict, store_id: str) -> dict[str, dict]:
    tcins = ",".join(p["tcin"] for p in cfg["target_products"])
    params = {
        "key": REDSKY_KEY, "channel": "WEB", "tcins": tcins,
        "store_id": store_id, "required_store_id": store_id,
        "has_required_store_id": "true", "scheduled_delivery_store_id": store_id,
        "zip": cfg["zip"], "state": cfg["state"],
        "latitude": cfg["latitude"], "longitude": cfg["longitude"],
        "page": "/p/-",
    }
    url = f"{REDSKY}/product_summary_with_fulfillment_v1?{urllib.parse.urlencode(params)}"
    try:
        data = http_json(url)
    except (urllib.error.URLError, TimeoutError):
        time.sleep(3)  # one quick retry; Target sometimes refuses a single request
        data = http_json(url)
    return parse_summaries(data, store_id)


def parse_search(data: dict) -> list[dict]:
    items = []
    for p in dig(data, "data", "search", "products", default=[]) or []:
        title = dig(p, "item", "product_description", "title", default="") or ""
        items.append({
            "tcin": str(p.get("tcin", "")),
            "title": title.replace("&#233;", "é"),
            "price": dig(p, "price", "current_retail"),
            "marketplace": bool(dig(p, "item", "fulfillment", "is_marketplace", default=False)),
        })
    return [i for i in items if i["tcin"]]


def fetch_search(cfg: dict) -> list[dict]:
    store_id = cfg["target_stores"][0]["id"]
    kw = cfg["discovery_search"]
    params = {
        "key": REDSKY_KEY, "channel": "WEB", "count": 24, "offset": 0,
        "keyword": kw, "page": f"/s/{kw}", "pricing_store_id": store_id,
        "visitor_id": "%032X" % random.getrandbits(128),
        "default_purchasability_filter": "false",
    }
    url = f"{REDSKY}/plp_search_v2?{urllib.parse.urlencode(params)}"
    return parse_search(http_json(url))


# --------------------------------------------------------------- the core

class Checker:
    def __init__(self, cfg: dict, state: dict, notifier: Notifier,
                 now_fn=None, target_fn=fetch_target, search_fn=fetch_search):
        self.cfg = cfg
        self.state = state
        self.n = notifier
        self.tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.target_fn = target_fn
        self.search_fn = search_fn
        state.setdefault("availability", {})   # "tcin|online" / "tcin|<store>" -> bool
        state.setdefault("items", {})          # tcin -> latest view for dashboard
        state.setdefault("events", [])
        state.setdefault("reminders_sent", {})
        state.setdefault("seen_tcins", [])
        state.setdefault("failures", 0)
        state.setdefault("checks_today", {"date": "", "count": 0})

    # -- events / dashboard
    def log(self, kind: str, text: str, url: str | None = None):
        self.state["events"].insert(0, {
            "at": self.now_fn().isoformat(timespec="seconds"),
            "kind": kind, "text": text, "url": url})
        del self.state["events"][MAX_EVENTS:]

    def transition(self, key: str, avail: bool) -> bool:
        """Record availability; True when it just became available."""
        prev = self.state["availability"].get(key)
        self.state["availability"][key] = avail
        # prev None = first time we've seen it: alert only if available
        # so a fresh install still tells you what's on shelves right now.
        return avail and prev is not True

    # -- one pass
    def run_pass(self):
        now = self.now_fn()
        cfg = self.cfg
        ok_calls = 0
        names = {p["tcin"]: p["name"] for p in cfg["target_products"]}
        online_done = False
        for store in cfg["target_stores"]:
            try:
                results = self.target_fn(cfg, store["id"])
                ok_calls += 1
            except Exception as exc:
                print(f"  ! Target fetch failed for {store['name']}: {exc}", file=sys.stderr)
                continue
            print(f"  {store['name']} ({store['id']}): {len(results)} products; " + ", ".join(
                f"{names.get(t, t)}={r['online']}/{r['store']}" for t, r in results.items()), flush=True)
            for tcin, r in results.items():
                name = names.get(tcin, r["title"] or tcin)
                item = self.state["items"].setdefault(tcin, {"name": name, "stores": {}})
                item["name"], item["url"] = name, product_url(tcin)
                item["stores"][store["name"]] = {"status": r["store"], "qty": r["store_qty"]}
                item["checked"] = now.isoformat(timespec="seconds")
                if not online_done:
                    item["online"] = r["online"]
                    if self.transition(f"{tcin}|online", r["online"] in AVAILABLE):
                        verb = "Pre-order open" if r["online"] == "PRE_ORDER_SELLABLE" else "IN STOCK online"
                        self.n.send(f"{verb}: {name}", "Target.com. Go now, drops sell out in minutes.",
                                    product_url(tcin), priority=5, tags=["rotating_light", "target"])
                        self.log("online", f"{name} {verb.lower()} at Target.com", product_url(tcin))
                in_store = r["store"] in AVAILABLE
                if self.transition(f"{tcin}|{store['id']}", in_store):
                    qty = f" ({r['store_qty']} available)" if r["store_qty"] else ""
                    self.n.send(f"{store['name']}: {name}{qty}",
                                f"Showing in stock at {store['name']}, {store['address']}. "
                                "Order pickup now or head over.",
                                product_url(tcin), priority=5, tags=["round_pushpin", "target"])
                    self.log("store", f"{name} in stock at {store['name']}{qty}", product_url(tcin))
            online_done = online_done or bool(results)

        self.discover(now)
        self.reminders(now)
        self.health(now, ok_calls)
        self.state["last_check"] = now.isoformat(timespec="seconds")

    def discover(self, now):
        # Target's search endpoint often refuses cloud servers, and new listings
        # don't need minute-level checks, so try it at most every 20 minutes.
        last = self.state.get("last_search")
        if last and now - datetime.fromisoformat(last) < timedelta(minutes=20):
            return
        self.state["last_search"] = now.isoformat(timespec="seconds")
        try:
            found = self.search_fn(self.cfg)
        except Exception as exc:
            print(f"  (new-listing search skipped: Target said {exc})", flush=True)
            return
        watched = {p["tcin"] for p in self.cfg["target_products"]}
        seen = set(self.state["seen_tcins"])
        first_run = not seen
        for it in found:
            if "30th" not in it["title"].lower() or it["tcin"] in watched or it["tcin"] in seen:
                continue
            seen.add(it["tcin"])
            if first_run or it["marketplace"]:
                continue
            price = f" at ${it['price']:.2f}" if isinstance(it["price"], (int, float)) else ""
            self.n.send("New 30th Celebration listing at Target",
                        f"{it['title']}{price}. New listings often go live right before a drop.",
                        product_url(it["tcin"]), priority=4, tags=["new", "target"])
            self.log("listing", f"New Target listing: {it['title']}{price}", product_url(it["tcin"]))
        self.state["seen_tcins"] = sorted(seen)

    def reminders(self, now):
        sent = self.state["reminders_sent"]
        today = now.strftime("%Y-%m-%d")
        day = DAYS[now.weekday()]
        for r in self.cfg.get("reminders", []):
            hh, mm = map(int, r["time"].split(":"))
            start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day in r["days"] and start <= now < start + timedelta(minutes=30) \
                    and sent.get(r["id"]) != today:
                sent[r["id"]] = today
                self.n.send(r["title"], r["message"], r.get("url"), priority=4, tags=["alarm_clock"])
                self.log("reminder", r["title"], r.get("url"))
        for r in self.cfg.get("release_days", []):
            rid = f"release-{r['date']}"
            if r["date"] == today and now.hour >= 6 and sent.get(rid) != today:
                sent[rid] = today
                self.n.send(r["title"], r["message"], None, priority=4, tags=["calendar"])
                self.log("reminder", r["title"])

    def health(self, now, ok_calls):
        st = self.state
        ct = st["checks_today"]
        today = now.strftime("%Y-%m-%d")
        if ct["date"] != today:
            ct.update(date=today, count=0)
        if ok_calls:
            if st["failures"] >= 10:
                self.n.send("Restock checker is back", "Target checks are working again.", priority=2)
            st["failures"] = 0
            ct["count"] += 1
            st["last_ok"] = now.isoformat(timespec="seconds")
        else:
            st["failures"] += 1
            if st["failures"] == 10:
                self.n.send("Restock checker can't reach Target",
                            "Target checks have failed 10 times in a row. Don't rely on silence "
                            "until you get the all-clear; keep TrackaLacker alerts on.",
                            priority=4, tags=["warning"])
                self.log("error", "Target checks failing")


def write_status(cfg, state, path):
    status = {
        "last_check": state.get("last_check"),
        "last_ok": state.get("last_ok"),
        "failing": state.get("failures", 0) >= 3,
        "checks_today": state["checks_today"]["count"],
        "stores": cfg["target_stores"],
        "items": [dict(tcin=p["tcin"], msrp=p["msrp"], **state["items"].get(p["tcin"], {"name": p["name"]}))
                  for p in cfg["target_products"]],
        "events": state["events"],
    }
    with open(path, "w") as f:
        json.dump(status, f, indent=1)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_json(os.path.join(here, "config.json"), None)
    state_path = os.environ.get("STATE_PATH", "state.json")
    status_path = os.environ.get("STATUS_PATH", "status.json")
    state = load_json(state_path, {})
    subs = json.loads(os.environ.get("WEBPUSH_SUBSCRIPTIONS") or "[]")
    notifier = Notifier(os.environ.get("NTFY_TOPIC") or None,
                        os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
                        subs if isinstance(subs, list) else [subs],
                        os.environ.get("VAPID_PRIVATE_KEY") or None)
    if os.environ.get("SEND_TEST_PUSH"):
        notifier.send("Restock Radar is connected", "You'll get alerts here when 30th Celebration stock shows up.",
                      "https://www.target.com/s?searchTerm=pokemon+30th+celebration", priority=3)
        return
    checker = Checker(cfg, state, notifier)
    loop_for = int(os.environ.get("LOOP_SECONDS", "0"))
    interval = int(os.environ.get("INTERVAL", "60"))
    deadline = time.monotonic() + loop_for
    while True:
        checker.run_pass()
        with open(state_path, "w") as f:
            json.dump(state, f)
        write_status(cfg, state, status_path)
        if time.monotonic() + interval > deadline:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
