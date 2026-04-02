#!/usr/bin/env python3
"""Quick GEX level updater — paste Menthor Q levels in seconds.

Usage:
  gexupdate                          # Interactive prompt
  gexupdate --auto                   # Fetch from Menthor Q API automatically
  gexupdate 5850 5650 5750           # call_wall put_wall hvl
  gexupdate 5850 5650 5750 5740      # + zero_gamma
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GEX_FILE = Path(__file__).parent / "gex_levels.json"


def update_levels(call_res=None, put_sup=None, hvl=None, zero_gamma=None,
                  dte0_call=None, dte0_put=None, em_lo=None, em_hi=None):
    data = {}
    if GEX_FILE.exists():
        try:
            data = json.loads(GEX_FILE.read_text())
        except Exception:
            pass

    if call_res:
        data["call_resistance"] = call_res
    if put_sup:
        data["put_support"] = put_sup
    if hvl:
        data["hvl"] = hvl
    if zero_gamma:
        data["zero_gamma"] = zero_gamma
    if dte0_call:
        data["0dte_call_resistance"] = dte0_call
    if dte0_put:
        data["0dte_put_support"] = dte0_put
    if em_lo:
        data["expected_move_low"] = em_lo
    if em_hi:
        data["expected_move_high"] = em_hi

    from datetime import datetime
    data["_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["_source"] = "manual"

    GEX_FILE.write_text(json.dumps(data, indent=4))
    print(f"\nGEX levels updated:")
    for k, v in data.items():
        if not k.startswith("_"):
            print(f"  {k}: {v}")
    print(f"\nSaved to {GEX_FILE}")


def parse_float(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def interactive():
    print("=== Menthor Q GEX Level Update ===")
    print("Enter levels from your daily report (press Enter to skip)\n")

    call_res = parse_float(input("Call Resistance (call wall): ").strip())
    put_sup = parse_float(input("Put Support (put wall):      ").strip())
    hvl = parse_float(input("HVL (high vol level):        ").strip())
    zero_gamma = parse_float(input("Zero Gamma (optional):       ").strip())
    dte0_call = parse_float(input("0DTE Call Resistance (opt):  ").strip())
    dte0_put = parse_float(input("0DTE Put Support (opt):      ").strip())
    em_lo = parse_float(input("Expected Move Low (opt):     ").strip())
    em_hi = parse_float(input("Expected Move High (opt):    ").strip())

    if not any([call_res, put_sup, hvl]):
        print("No levels entered. Exiting.")
        return

    update_levels(call_res, put_sup, hvl, zero_gamma, dte0_call, dte0_put, em_lo, em_hi)


def auto_fetch():
    """Fetch GEX levels from Menthor Q API automatically."""
    import requests
    from bs4 import BeautifulSoup

    user = os.environ.get("MENTHORQ_USER", "")
    pwd = os.environ.get("MENTHORQ_PASS", "")
    if not user or not pwd:
        print("ERROR: MENTHORQ_USER / MENTHORQ_PASS not set in .env")
        sys.exit(1)

    # Skip weekends
    day = datetime.now()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    date_str = day.strftime("%Y-%m-%d")

    print(f"Fetching GEX levels from Menthor Q for {date_str} ...")

    # Login
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    login_page = session.get("https://menthorq.com/wp-login.php", timeout=10)
    soup = BeautifulSoup(login_page.text, "html.parser")
    form_data = {
        "log": user, "pwd": pwd, "wp-submit": "Log In",
        "redirect_to": "https://menthorq.com/", "testcookie": "1",
    }
    form = soup.find("form", {"id": "loginform"})
    if form:
        for inp in form.find_all("input", {"type": "hidden"}):
            name = inp.get("name")
            if name:
                form_data[name] = inp.get("value", "")
    resp = session.post("https://menthorq.com/wp-login.php", data=form_data,
                        timeout=15, allow_redirects=True)
    if "wp-login.php?action=" in resp.url:
        print("ERROR: Login failed — check credentials")
        sys.exit(1)

    # Get QDataParams nonce
    nonce_resp = session.get(
        "https://menthorq.com/account/?action=data&type=dashboard&commands=futures&tickers=futures",
        timeout=15,
    )
    m = re.search(r'var QDataParams\s*=\s*\{[^}]*"nonce"\s*:\s*"([a-f0-9]+)"', nonce_resp.text)
    if not m:
        print("ERROR: Could not find API nonce")
        sys.exit(1)
    nonce = m.group(1)

    # Fetch key_levels (try today, fall back to previous trading days)
    raw = None
    for attempt in range(5):
        resp = session.post(
            "https://menthorq.com/wp-admin/admin-ajax.php",
            data={
                "action": "get_command", "security": nonce,
                "command_slug": "key_levels", "date": date_str,
                "is_intraday": "false", "ticker": "es1!",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=15,
        )
        result = resp.json()
        if isinstance(result, dict) and result.get("success"):
            raw = result["data"]["resource"]["data"]
            print(f"  Got data for {date_str}")
            break
        # Try previous trading day
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        date_str = day.strftime("%Y-%m-%d")

    if not raw:
        print("ERROR: No data available from Menthor Q for recent trading days")
        sys.exit(1)
    field_map = {
        "Call Resistance": "call_resistance",
        "Put Support": "put_support",
        "High Vol Level": "hvl",
        "HVL": "hvl",
        "Call Resistance 0DTE": "0dte_call_resistance",
        "Put Support 0DTE": "0dte_put_support",
        "1D Max.": "expected_move_high",
        "1D Min.": "expected_move_low",
    }
    data = {}
    for mq_key, our_key in field_map.items():
        val = raw.get(mq_key)
        if val is not None:
            try:
                data[our_key] = float(str(val).replace(",", "").replace("M", "e6").replace("B", "e9").rstrip("%"))
            except (ValueError, TypeError):
                pass

    if not data:
        print("ERROR: No levels parsed from API response")
        sys.exit(1)

    data["_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["_source"] = "menthorq_api"
    GEX_FILE.write_text(json.dumps(data, indent=4))

    print("GEX levels updated from Menthor Q:")
    for k, v in data.items():
        if not k.startswith("_"):
            print(f"  {k}: {v}")
    print(f"\nSaved to {GEX_FILE}")


def main():
    args = sys.argv[1:]
    if not args:
        interactive()
    elif args[0] == "--auto":
        auto_fetch()
    elif len(args) >= 3:
        call_res = parse_float(args[0])
        put_sup = parse_float(args[1])
        hvl = parse_float(args[2])
        zero_gamma = parse_float(args[3]) if len(args) > 3 else None
        update_levels(call_res, put_sup, hvl, zero_gamma)
    else:
        print("Usage:")
        print("  gexupdate                    # Interactive")
        print("  gexupdate --auto             # Fetch from Menthor Q API")
        print("  gexupdate 5850 5650 5750     # call_wall put_wall hvl")
        print("  gexupdate 5850 5650 5750 5740  # + zero_gamma")


if __name__ == "__main__":
    main()
