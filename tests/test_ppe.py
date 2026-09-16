"""PPE charge-limit test: prove undelivered reviews are NOT marked seen and
retry on the next run (Fable audit section 3.3).

NOTE: the local storage emulation rewrites dataset file numbering from 1 in
every process, so each scenario runs in a FRESH storage dir. The "recovery"
scenario pre-seeds the named-KVS state record with the IDs the "limited"
scenario delivered (passed via <storage>-seen.json), which mirrors how the
persistent named store behaves on the platform.

    # run 1: tight charge cap -> partial delivery
    ACTOR_TEST_PAY_PER_EVENT=true ACTOR_MAX_TOTAL_CHARGE_USD=5 \
      no_proxy="localhost,127.0.0.1" APIFY_LOCAL_STORAGE_DIR=<dirA> \
      python tests/test_ppe.py limited
    # run 2: cap lifted, delta -> remainder delivered, no duplicates
    ACTOR_TEST_PAY_PER_EVENT=true \
      no_proxy="localhost,127.0.0.1" APIFY_LOCAL_STORAGE_DIR=<dirB> \
      python tests/test_ppe.py recovery

Env: REVIEW_FIREHOSE_BASE_URL is set here (local fixture server).
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
FIX = os.path.join(ROOT, "tests", "fixtures")
CACHE = {}
PAGE_NAMES = {None: "whatsapp-us-seeall.html", "iphone": "whatsapp-us-iphone.html",
              "mac": "whatsapp-us-mac.html"}
STATE_KEY = "review-firehose-state-310633997"


def page_for(query):
    qs = parse_qs(query)
    if qs.get("see-all") != ["reviews"]:
        return None
    name = PAGE_NAMES.get(qs.get("platform", [None])[0])
    if name not in CACHE:
        with open(os.path.join(FIX, name), encoding="utf-8") as f:
            CACHE[name] = f.read()
    return CACHE[name]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/us/app/id310633997":
            body = page_for(u.query)
            if body is None:
                self.send_response(404); self.end_headers(); return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def read_items(storage):
    ds_dir = os.path.join(storage, "datasets", "default")
    items = []
    for fn in sorted(os.listdir(ds_dir)):
        if fn == "__metadata__.json" or not fn.endswith(".json"):
            continue
        with open(os.path.join(ds_dir, fn), encoding="utf-8") as f:
            items.append(json.load(f))
    return items


def read_state(storage):
    p = os.path.join(storage, "key_value_stores", "review-firehose", STATE_KEY)
    if not os.path.exists(p):
        p += ".json"
    return json.load(open(p))


def write_seed_state(storage, seen_ids):
    # Replicate the SDK's local record layout: extensionless data file plus
    # the .__metadata__.json sidecar the local client needs to read it back.
    d = os.path.join(storage, "key_value_stores", "review-firehose")
    os.makedirs(d, exist_ok=True)
    payload = json.dumps({"seen": sorted(seen_ids), "ratings": {}}).encode()
    with open(os.path.join(d, STATE_KEY), "wb") as f:
        f.write(payload)
    with open(os.path.join(d, STATE_KEY + ".__metadata__.json"), "w") as f:
        json.dump({"key": STATE_KEY,
                   "content_type": "application/json; charset=utf-8",
                   "size": len(payload)}, f)


def main():
    scenario = sys.argv[1]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["REVIEW_FIREHOSE_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"

    storage = os.environ["APIFY_LOCAL_STORAGE_DIR"]
    seen_file = sys.argv[2] if len(sys.argv) > 2 else storage + "-seen.json"
    kvs = os.path.join(storage, "key_value_stores", "default")
    os.makedirs(kvs, exist_ok=True)
    mode = "full" if scenario == "limited" else "delta"
    with open(os.path.join(kvs, "INPUT.json"), "w") as f:
        json.dump({"appId": "310633997", "countries": ["us"], "mode": mode}, f)

    seeded = set()
    if scenario == "recovery":
        seeded = set(json.load(open(seen_file)))
        assert seeded, "limited run must have written seen IDs first"
        write_seed_state(storage, seeded)

    import asyncio
    import main as M  # noqa: E402
    try:
        asyncio.run(M.main())
    except SystemExit as e:
        assert e.code == 0, f"actor exited with code {e.code}"

    items = read_items(storage)
    state = read_state(storage)
    ids = {i["reviewId"] for i in items}
    print(f"SCENARIO={scenario} dataset_items={len(items)} seen={len(state['seen'])} "
          f"seen==delivered={set(state['seen']) == ids}")

    if scenario == "limited":
        assert 0 < len(items) < 30, f"expected partial delivery, got {len(items)}"
        # THE fix: only delivered IDs may be marked seen
        assert set(state["seen"]) == ids, \
            "seen contains IDs that were never delivered (data loss bug)"
        with open(seen_file, "w") as f:
            json.dump(sorted(ids), f)
        print("PPE limited PASSED: partial delivery, no phantom seen IDs")
    else:
        assert len(items) == 30 - len(seeded), \
            f"expected the {30 - len(seeded)} undelivered reviews, got {len(items)}"
        assert len(ids) == len(items), "duplicate items pushed"
        assert not (ids & seeded), "recovery re-pushed already-delivered reviews"
        assert len(state["seen"]) == 30, "all 30 IDs now marked seen"
        assert ids <= set(state["seen"]), "delivered IDs must be marked seen"
        print("PPE recovery PASSED: remainder delivered exactly once, none lost")


if __name__ == "__main__":
    main()
