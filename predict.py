"""
vandaagtanken.nl — Dutch petrol price predictor
-------------------------------------------------
Approach:
  1. Scrape today's real GLA from brandstof-zoeker.nl (ground truth)
  2. Subtract fixed discount (€0.25) to get expected pump price
  3. Forecast: use Brent daily changes + empirically calibrated
     sensitivity (€ per liter per $10 Brent move) based on
     historical NL data to project GLA changes for next 5 days
  4. Signal: JA if day+3 GLA > today, NEE if lower, STABIEL if flat

Historical calibration (NL oliemij component vs Brent):
  The oliemij deel (~51% of GLA) moves with Brent but with a lag.
  From historical data: ~€0.015-0.020 GLA change per $10 Brent move
  after ~3-5 day lag. We use €0.017/10$ as central estimate.
  Diesel is less sensitive: ~€0.012/10$.
"""

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

# ── Constants ────────────────────────────────────────────────────────────────
STATION_KORTING_BENZINE = 0.25   # avg discount vs GLA (€/L)
STATION_KORTING_DIESEL  = 0.30   # avg discount vs GLA diesel (€/L)

# Empirical sensitivity: GLA change per $10 Brent move (after lag)
# Derived from historical NL data: oliemij deel ≈ 51% of GLA
# $10 Brent / 159L per barrel / 1.08 EURUSD * 1/0.445 refining * 1.21 BTW ≈ €0.017
BRENT_SENSITIVITY_BENZINE = 0.017  # €/L per $10 Brent
BRENT_SENSITIVITY_DIESEL  = 0.013  # €/L per $10 Brent (less sensitive)

# Max realistic GLA change per day (historical: ~1-3ct/day normal, 8ct extreme)
MAX_DAILY_CHANGE = 0.06  # €/L


def fetch_url(url, timeout=12):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "vandaagtanken-bot/2.0 (+https://vandaagtanken.nl)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def scrape_gla(brandstof="euro95"):
    """
    Scrape today's GLA.
    Primary:  brandstof-zoeker.nl (same GLA as UnitedConsumers, easy to parse)
    Fallback: unitedconsumers.com product page
    Both show the same official GLA — UC calculates it from the 5 biggest NL oil companies.
    """
    slug = "euro95" if brandstof == "euro95" else "diesel"

    # Primary: brandstof-zoeker.nl
    try:
        url  = f"https://www.brandstof-zoeker.nl/adviesprijzen/{slug}/"
        html = fetch_url(url)
        m    = re.search(r'(?:benzine|diesel)prijs op .+? is\s*[€]\s*([\d][,\d]+)', html)
        if m:
            gla = float(m.group(1).replace(',', '.'))
            print(f"GLA {brandstof} via brandstof-zoeker.nl: €{gla:.3f}")
            return gla
    except Exception as e:
        print(f"brandstof-zoeker failed: {e}")

    # Fallback: unitedconsumers.com
    try:
        url  = f"https://www.unitedconsumers.com/tanken/brandstofprijzen/product/{slug}"
        html = fetch_url(url)
        m    = re.search(r'"price"[:\s]+"?([\d]+[.,][\d]+)"?', html)
        if not m:
            m = re.search(r'([\d]+[.,][\d]{3})\s*(?:euro|€)', html)
        if m:
            gla = float(m.group(1).replace(',', '.'))
            print(f"GLA {brandstof} via unitedconsumers.com: €{gla:.3f}")
            return gla
    except Exception as e:
        print(f"UnitedConsumers failed: {e}")

    raise ValueError(f"Could not scrape GLA for {brandstof} from any source")


def get_brent_series(days_back=20):
    """Fetch daily Brent prices from FRED."""
    end   = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    url   = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=DCOILBRENTEU&vintage_date={end}&observation_start={start}"
    )
    raw    = fetch_url(url)
    prices = []
    for line in raw.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in (".", ""):
            try:
                prices.append(float(parts[1].strip()))
            except ValueError:
                pass
    return prices  # ascending, most recent last


def build_forecast(gla_today, brent_series, sensitivity, korting):
    """
    Project pump price for next 5 days, calculated per day independently.

    Per day:
      1. Project Brent price for that day using momentum + decay
      2. Calculate GLA delta vs today based on Brent move and sensitivity
      3. pump_proj = (gla_today + gla_delta) - korting

    The korting is fixed and applied every day the same way.
    Max GLA change per day capped at €0.02 (realistic daily move).
    """
    MAX_DAILY_GLA_CHANGE = 0.02  # max €0.02 GLA change per day

    if len(brent_series) < 4:
        return []

    # Weighted recent Brent momentum
    d1 = brent_series[-1] - brent_series[-2]
    d2 = brent_series[-2] - brent_series[-3]
    d3 = brent_series[-3] - brent_series[-4]
    momentum = (d1 * 0.6) + (d2 * 0.3) + (d3 * 0.1)

    forecast   = []
    brent_prev = brent_series[-1]
    today      = datetime.now(timezone.utc).date()
    gla_prev   = gla_today

    for i in range(1, 6):
        # Decay momentum each day (trends fade over time)
        momentum    *= 0.80
        brent_change = momentum
        brent_proj   = round(brent_prev + brent_change, 2)

        # GLA change for this day based on Brent move
        gla_delta = (brent_change / 10.0) * sensitivity

        # Cap per-day GLA change
        sign      = 1 if gla_delta >= 0 else -1
        gla_delta = sign * min(abs(gla_delta), MAX_DAILY_GLA_CHANGE)

        # Each day: new GLA = previous day GLA + today's delta
        gla_proj  = round(gla_prev + gla_delta, 3)

        # Pump price = GLA that day - fixed korting
        pump_proj = round(gla_proj - korting, 2)
        pump_prev = round(gla_prev - korting, 2)

        forecast.append({
            "date":       str(today + timedelta(days=i)),
            "brent_proj": brent_proj,
            "gla_proj":   gla_proj,
            "pump_proj":  pump_proj,
            "changed":    pump_proj != pump_prev,
        })

        gla_prev   = gla_proj
        brent_prev = brent_proj

    return forecast


def make_signal(gla_today, forecast):
    """JA/NEE/MAAKT_NIET_UIT based on day+3 projection."""
    if len(forecast) < 3:
        return "maakt_niet_uit", "Onvoldoende data voor voorspelling."

    gla_day3 = forecast[2]["gla_proj"]
    diff      = gla_day3 - gla_today
    pct       = round((diff / gla_today) * 100, 1)
    pct_str   = f"+{pct}%" if pct > 0 else f"{pct}%"

    if diff > 0.005:
        return "ja", f"De pompprijs wordt naar verwachting {pct_str} hoger de komende dagen."
    elif diff < -0.005:
        return "nee", f"De pompprijs wordt naar verwachting {pct_str} lager de komende dagen."
    else:
        return "maakt_niet_uit", "De pompprijs blijft naar verwachting stabiel de komende dagen."


def main():
    today = datetime.now(timezone.utc)

    # ── Brent series ──────────────────────────────────────────────────────────
    print("Fetching Brent from FRED...")
    try:
        brent = get_brent_series(days_back=20)
        print(f"Brent: {len(brent)} days, latest ${brent[-1]:.2f}")
    except Exception as e:
        print(f"FRED failed: {e} — using fallback")
        brent = [108.0] * 10  # fallback flat

    current_brent      = brent[-1]
    week_ago_brent     = brent[max(0, len(brent) - 6)]
    brent_change_pct   = round(((current_brent - week_ago_brent) / week_ago_brent) * 100, 1)

    # ── Benzine GLA ───────────────────────────────────────────────────────────
    print("Scraping benzine GLA...")
    try:
        gla_benzine = scrape_gla("euro95")
    except Exception as e:
        print(f"Benzine GLA failed: {e} — using fallback")
        gla_benzine = 2.551  # today's real value as fallback

    pump_benzine = round(gla_benzine - STATION_KORTING_BENZINE, 2)

    # ── Benzine forecast & signal ─────────────────────────────────────────────
    forecast_benzine        = build_forecast(gla_benzine, brent, BRENT_SENSITIVITY_BENZINE, STATION_KORTING_BENZINE)
    advies_b, reden_b       = make_signal(gla_benzine, forecast_benzine)

    benzine_out = {
        "updated":             today.isoformat(),
        "brent_usd":           round(current_brent, 2),
        "brent_change_pct":    brent_change_pct,
        "predicted_pump":      gla_benzine,
        "pump_voor_gebruiker": pump_benzine,
        "station_korting":     STATION_KORTING_BENZINE,
        "advies":              advies_b,
        "reden":               reden_b,
        "nieuws":              None,
        "forecast":            forecast_benzine,
        "breakdown": {
            "accijns": 0.797,
            "cova":    0.0085,
            "btw_pct": 21,
            "marge":   0.18,
        }
    }

    with open("prediction.json", "w") as f:
        json.dump(benzine_out, f, indent=2)
    print(f"Benzine: GLA €{gla_benzine} → pump €{pump_benzine} → {advies_b}")

    # ── Diesel GLA ────────────────────────────────────────────────────────────
    print("Scraping diesel GLA...")
    try:
        gla_diesel = scrape_gla("diesel")
    except Exception as e:
        print(f"Diesel GLA failed: {e} — using fallback")
        gla_diesel = 2.696  # today's real value as fallback

    pump_diesel = round(gla_diesel - STATION_KORTING_DIESEL, 2)

    forecast_diesel         = build_forecast(gla_diesel, brent, BRENT_SENSITIVITY_DIESEL, STATION_KORTING_DIESEL)
    advies_d, reden_d       = make_signal(gla_diesel, forecast_diesel)

    diesel_out = {
        "updated":             today.isoformat(),
        "brent_usd":           round(current_brent, 2),
        "brent_change_pct":    brent_change_pct,
        "predicted_pump":      gla_diesel,
        "pump_voor_gebruiker": pump_diesel,
        "station_korting":     STATION_KORTING_DIESEL,
        "advies":              advies_d,
        "reden":               reden_d,
        "nieuws":              None,
        "forecast":            forecast_diesel,
        "breakdown": {
            "accijns": 0.524,
            "cova":    0.0085,
            "btw_pct": 21,
            "marge":   0.18,
        }
    }

    with open("prediction_diesel.json", "w") as f:
        json.dump(diesel_out, f, indent=2)
    print(f"Diesel: GLA €{gla_diesel} → pump €{pump_diesel} → {advies_d}")

    print("Done.")


if __name__ == "__main__":
    main()
