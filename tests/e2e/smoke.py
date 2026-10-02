import sys, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    logs = []
    pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto("http://localhost:8765/")
    try:
        pg.wait_for_selector("body[data-ready='1']", timeout=60000)
    except Exception as e:
        print("TIMEOUT", e)
    print("\n".join(logs[:20]))
    print(pg.inner_text("#status")[:300], "|", pg.inner_text("#data-meta"))
    pg.screenshot(path="/tmp/smoke.png", full_page=True)
    b.close()
