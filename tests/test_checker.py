import json
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import checker  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))


def summary(tcin, online="OUT_OF_STOCK", store_id="1028", pickup="UNAVAILABLE", qty=0):
    return {"tcin": tcin,
            "item": {"product_description": {"title": "Pokémon 30th " + tcin}},
            "fulfillment": {"shipping_options": {"availability_status": online},
                            "store_options": [{"location_id": store_id,
                                               "location_available_to_promise_quantity": qty,
                                               "order_pickup": {"availability_status": pickup},
                                               "in_store_only": {"availability_status": "NOT_SOLD_IN_STORE"}}]}}


class Fake:
    def __init__(self):
        self.online = {}
        self.stores = {}
        self.search = []
        self.fail = False

    def target(self, cfg, store_id):
        if self.fail:
            raise OSError("blocked")
        return checker.parse_summaries({"data": {"product_summaries": [
            summary(p["tcin"], self.online.get(p["tcin"], "OUT_OF_STOCK"), store_id,
                    "IN_STOCK" if (p["tcin"], store_id) in self.stores else "UNAVAILABLE",
                    3 if (p["tcin"], store_id) in self.stores else 0)
            for p in cfg["target_products"]]}}, store_id)

    def find(self, cfg):
        return self.search


def make(now=datetime(2026, 10, 7, 14, 0, tzinfo=TZ)):
    fake, state, n = Fake(), {}, checker.Notifier(None)
    clock = {"now": now}
    c = checker.Checker(CFG, state, n, now_fn=lambda: clock["now"],
                        target_fn=fake.target, search_fn=fake.find)
    return c, fake, n, clock


class Tests(unittest.TestCase):
    def test_parse(self):
        r = checker.parse_summaries({"data": {"product_summaries": [summary("1", "IN_STOCK", "1028", "IN_STOCK", 4)]}}, "1028")
        self.assertEqual(r["1"], {"title": "Pokémon 30th 1", "online": "IN_STOCK", "store": "IN_STOCK", "store_qty": 4})

    def test_quiet_when_nothing_in_stock(self):
        c, fake, n, _ = make()
        c.run_pass(); c.run_pass()
        self.assertEqual(n.sent, [])

    def test_online_alert_once(self):
        c, fake, n, _ = make()
        c.run_pass()
        fake.online["1010892076"] = "IN_STOCK"
        c.run_pass(); c.run_pass()
        self.assertEqual(len(n.sent), 1)
        self.assertIn("Elite Trainer Box", n.sent[0]["title"])
        self.assertEqual(n.sent[0]["priority"], 5)
        self.assertEqual(n.sent[0]["click"], "https://www.target.com/p/-/A-1010892076")
        # sells out then comes back -> alert again
        fake.online.clear(); c.run_pass()
        fake.online["1010892076"] = "IN_STOCK"; c.run_pass()
        self.assertEqual(len(n.sent), 2)

    def test_store_alert_names_store(self):
        c, fake, n, _ = make()
        c.run_pass()
        fake.stores = {("1011407490", "2147")}
        c.run_pass()
        self.assertEqual(len(n.sent), 1)
        self.assertTrue(n.sent[0]["title"].startswith("Target West Covina South: Booster Bundle"))

    def test_discovery_skips_first_run_and_marketplace(self):
        c, fake, n, clock = make()
        fake.search = [{"tcin": "999", "title": "30th Celebration Old", "price": 20.0, "marketplace": False}]
        c.run_pass()
        clock["now"] = clock["now"] + checker.timedelta(minutes=21)
        self.assertEqual(n.sent, [])
        fake.search += [{"tcin": "111", "title": "Pokemon 30th Celebration Ultra-Premium Collection", "price": 179.99, "marketplace": False},
                        {"tcin": "222", "title": "Pokemon 30th Celebration ETB 2-pack", "price": 139.0, "marketplace": True},
                        {"tcin": "333", "title": "Pokemon Scarlet ETB", "price": 49.99, "marketplace": False}]
        c.run_pass()
        self.assertEqual(len(n.sent), 1)
        self.assertIn("Ultra-Premium", n.sent[0]["message"])

    def test_friday_reminder_once(self):
        c, fake, n, clock = make(datetime(2026, 10, 9, 7, 41, tzinfo=TZ))  # a Friday
        c.run_pass(); c.run_pass()
        self.assertEqual([s["title"] for s in n.sent], ["Target vendor morning"])
        clock["now"] = datetime(2026, 10, 9, 9, 0, tzinfo=TZ)
        c.run_pass()
        self.assertEqual(len(n.sent), 1)

    def test_failure_alert_after_ten(self):
        c, fake, n, _ = make()
        fake.fail = True
        for _ in range(12):
            c.run_pass()
        self.assertEqual([s["title"] for s in n.sent], ["Restock checker can't reach Target"])
        fake.fail = False
        c.run_pass()
        self.assertEqual(n.sent[-1]["title"], "Restock checker is back")

    def test_status_file(self):
        c, fake, n, _ = make()
        fake.online["1010892076"] = "IN_STOCK"
        c.run_pass()
        path = "/tmp/_status_test.json"
        checker.write_status(CFG, c.state, path)
        with open(path) as f:
            st = json.load(f)
        etb = st["items"][0]
        self.assertEqual(etb["online"], "IN_STOCK")
        self.assertIn("Target West Covina", etb["stores"])
        self.assertEqual(st["events"][0]["kind"], "online")


if __name__ == "__main__":
    unittest.main()
