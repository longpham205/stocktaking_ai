"""End-to-end check of a running API over real HTTP (`make smoke`): login -> order -> basket photo ->
job -> order lines with boxes and signed images -> fix flagged and unpriced lines -> pay -> history,
reports, evidence test, advanced settings, staff kept out of admin.

Needs two accounts and a catalog in the API's database. The passwords come from the environment and
are never printed:

    SMOKE_ADMIN=admin SMOKE_ADMIN_PW=... SMOKE_STAFF=staff SMOKE_STAFF_PW=... \\
        uv run python scripts/smoke_api.py data_demo/query/query_01.jpg

The order it pays stays in the database (a real sale on the day's report).
"""

import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("SMOKE_API", "http://127.0.0.1:8000")


def check(condition: bool, what: str) -> None:
    print(("OK   " if condition else "FAIL ") + what)
    if not condition:
        raise SystemExit(1)


def client(user: str, password: str) -> httpx.Client:
    response = httpx.post(f"{BASE}/api/auth/login", json={"username": user, "password": password}, timeout=30)
    check(response.status_code == 200, f"login {user}")
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {response.json()['token']}"}, timeout=120)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_api.py <basket photo>")
    photo = Path(sys.argv[1]).read_bytes()
    health = httpx.get(f"{BASE}/api/health", timeout=10).json()
    check(health["status"] == "ready", f"health, recognizer={health['recognizer']}")
    admin = client(os.environ["SMOKE_ADMIN"], os.environ["SMOKE_ADMIN_PW"])
    staff = client(os.environ["SMOKE_STAFF"], os.environ["SMOKE_STAFF_PW"])
    catalog = admin.get("/api/admin/products").json()
    check(catalog["total"] > 0, f"catalog: {catalog['total']} products, {catalog['missing_price']} without a price")

    order = staff.post("/api/orders").json()
    started = time.monotonic()
    headers = {"Idempotency-Key": f"smoke-{order['id']}"}
    submitted = staff.post(f"/api/orders/{order['id']}/captures", content=photo, headers=headers)
    check(submitted.status_code == 202, "photo accepted")
    again = staff.post(f"/api/orders/{order['id']}/captures", content=photo, headers=headers)
    check(again.json() == {**submitted.json(), "duplicate": True}, "same Idempotency-Key -> same job")
    while (job := staff.get(f"/api/jobs/{submitted.json()['job_id']}").json())["status"] not in ("done", "error"):
        time.sleep(0.2)
    check(
        job["status"] == "done",
        f"job done in {time.monotonic() - started:.1f}s: {job.get('added')} objects, warnings {job.get('warnings')}",
    )
    view = job["order"]
    check(len(view["captures"]) == 1, f"{len(view['items'])} lines, {view['item_count']} objects")
    capture = view["captures"][0]
    check(
        httpx.get(BASE + capture["image_url"], timeout=30).status_code == 200,
        f"signed photo {capture['width']}x{capture['height']}, {len(capture['boxes'])} boxes",
    )
    thumbs = [i["thumbnail_url"] for i in view["items"] if i["thumbnail_url"]]
    if thumbs:
        check(httpx.get(BASE + thumbs[0], timeout=30).status_code == 200, "signed thumbnail")
        check(httpx.get(BASE + thumbs[0][:-3] + "AAA", timeout=30).status_code == 403, "forged signature -> 403")

    if not view["items"]:  # the recognizer found nothing it knows: add one product by hand
        pid = catalog["items"][0]["id"]
        staff.post(f"/api/orders/{order['id']}/items", json={"product_id": pid})
    for line in staff.get(f"/api/orders/{order['id']}").json()["items"]:
        if line["flagged"]:
            staff.patch(f"/api/orders/{order['id']}/items/{line['id']}", json={"confirm": True})
        if line["price_missing"]:
            staff.patch(f"/api/orders/{order['id']}/items/{line['id']}", json={"manual_price": 1000})
    view = staff.get(f"/api/orders/{order['id']}").json()
    check(view["flagged_count"] == 0 and view["missing_price_count"] == 0, f"lines fixed, total {view['total']}")
    paid = staff.post(
        f"/api/orders/{order['id']}/checkout", json={"method": "cash", "cash_given": view["total"] + 5000}
    )
    check(paid.status_code == 200 and paid.json()["change_given"] == 5000, "paid in cash, change 5000")
    check(staff.get("/api/history").json()["items"][0]["id"] == order["id"], "the sale is in the history")
    report = admin.get("/api/admin/reports?range=7d").json()
    check(
        report["revenue_today"] >= view["total"],
        f"report: {report['orders_today']} orders today, {report['revenue_today']} VND",
    )
    tested = admin.post("/api/admin/evidence-test", content=photo)
    check(tested.status_code == 200, f"evidence test: {len(tested.json()['items'])} objects")
    config = admin.get("/api/admin/config").json()
    check(config["config_error"] is None, f"advanced settings: {len(config['items'])}, {config['pipeline_config']}")
    check(staff.get("/api/admin/reports").status_code == 403, "staff kept out of admin")
    print("SMOKE PASSED")


if __name__ == "__main__":
    main()
