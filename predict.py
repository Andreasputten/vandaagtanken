"""
vandaagtanken.nl — Dutch petrol price predictor
-------------------------------------------------
Formula based on real NL pump price composition (brandstof-zoeker.nl / ANWB):

  GLA = accijns + BTW + oliemaatschappij_deel
  Pump price ≈ GLA − gemiddelde_korting

Where oliemaatschappij_deel is derived from:
  Brent ($/barrel) → convert to €/litre → apply refining yield → add costs + marge

Data sources (all free, no API key):
  - Brent:              FRED (St. Louis Fed)       https://fred.stlouisfed.org
  - EUR/USD:            ECB Data Portal            https://data.ecb.europa.eu
  - GLA cross-check:    brandstof-zoeker.nl        (scraped daily)
  - Nieuws:             Reuters / EIA / OilPrice   (RSS feeds, geen key)
"""

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

# ── Fixed NL tax constants (current, verified vs brandstof-zoeker.nl) ───────
# Verified 30-03-2026: GLA €2,570 = accijns €0,797 + BTW €0,446 + oliemij €1,327
ACCIJNS_PER_LITRE   = 0.797    # € excise duty Euro 95 (incl. tijdelijke korting 2026)
COVA_PER_LITRE      = 0.0085   # € strategic reserve levy (COVA)
BTW_RATE            = 0.21     # 21% VAT
BARREL_TO_LITRES    = 158.987  # 1 barrel = 158.987 litres
REFINING_YIELD      = 0.445    # ~44.5% of a barrel becomes usable petrol
REFINING_COST       = 0.055    # €/L refining + transport
MARGE               = 0.18     # €/L oil company + station margin
STATION_KORTING     = 0.32     # avg discount vs GLA at non-highway stations
# Price lag: Brent → NL pump (days). Rises faster than falls (asymmetric).
LAG_RISING          = 4        # days until price rise hits pump
LAG_FALLING         = 6        # days until price drop hits pump


def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "vandaagtanken-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def get_gla_brandstofzoeker():
    """
    Scrape the current GLA (Gemiddelde Landelijke Adviesprijs) for Euro 95
    from brandstof-zoeker.nl. Returns dict with gla, accijns, btw, oliemij or None.
    """
    try:
        html = fetch_url("https://www.brandstof-zoeker.nl/adviesprijzen/euro95/")
        # Extract GLA: "De benzineprijs op DD maand YYYY is € X,XXX"
        gla_match = re.search(r'benzineprijs op .+? is\s*[€]\s*([\d,]+)', html)
        # Extract breakdown table values (€ X,XXX pattern, 3 times in the table)
        prices = re.findall(r'€\s*([\d][,\d]+)', html)
        # Filter to plausible per-litre values (0.1 – 3.0)
        parsed = []
        for p in prices:
            try:
                val = float(p.replace(',', '.'))
                if 0.1 < val < 5.0:
                    parsed.append(val)
            except ValueError:
                pass

        gla = None
        if gla_match:
            gla = float(gla_match.group(1).replace(',', '.'))

        # The breakdown table rows are: oliemij, BTW, accijns (in that order)
        # and GLA total. We need at least 4 values.
        if gla and len(parsed) >= 3:
            # Find the three that sum closest to gla
            oliemij = btw = accijns = None
            for i in range(len(parsed) - 2):
                s = parsed[i] + parsed[i+1] + parsed[i+2]
                if abs(s - gla) < 0.005:
                    oliemij, btw, accijns = parsed[i], parsed[i+1], parsed[i+2]
                    break

            result = {
                "gla":     round(gla, 3),
                "oliemij": round(oliemij, 3) if oliemij else None,
                "btw":     round(btw, 3) if btw else None,
                "accijns": round(accijns, 3) if accijns else None,
                "source":  "brandstof-zoeker.nl",
            }
            print(f"Cross-check GLA: €{gla:.3f} (oliemij €{oliemij}, BTW €{btw}, accijns €{accijns})")
            return result
        elif gla:
            print(f"Cross-check GLA: €{gla:.3f} (breakdown not parsed)")
            return {"gla": gla, "source": "brandstof-zoeker.nl"}
    except Exception as e:
        print(f"brandstof-zoeker scrape failed: {e}")
    return None


def get_brent_series(days_back=30):
    """Fetch daily Brent prices from FRED (no API key needed for JSON)."""
    end   = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=DCOILBRENTEU&vintage_date={end}&observation_start={start}"
    )
    raw = fetch_url(url)
    prices = []
    for line in raw.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in (".", ""):
            try:
                prices.append({
                    "date":  parts[0].strip(),
                    "brent": float(parts[1].strip())
                })
            except ValueError:
                pass
    return prices  # ascending by date


def get_eurusd():
    """Fetch latest EUR/USD from ECB Data Portal (free, no key)."""
    url = (
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
        "?lastNObservations=5&format=jsondata"
    )
    raw = fetch_url(url)
    data = json.loads(raw)
    obs = data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]
    # observations is dict of index → [value, ...]
    latest_key = max(obs.keys(), key=lambda k: int(k))
    return float(obs[latest_key][0])


def brent_to_nl_pump(brent_usd_per_barrel, eurusd):
    """
    Convert Brent $/barrel to estimated NL Euro 95 pump price €/litre.

    Steps:
      1. Convert barrel to litres: brent / 158.987
      2. Convert USD to EUR: / eurusd
      3. Apply refining yield (only ~44.5% becomes petrol)
      4. Add refining & transport cost
      5. Add fixed taxes: accijns + COVA
      6. Add marge
      7. Apply BTW 21%
    """
    crude_eur_per_litre   = (brent_usd_per_barrel / BARREL_TO_LITRES) / eurusd
    product_ex_refining   = crude_eur_per_litre / REFINING_YIELD
    product_price         = product_ex_refining + REFINING_COST
    pre_btw               = product_price + ACCIJNS_PER_LITRE + COVA_PER_LITRE + MARGE
    pump_price            = pre_btw * (1 + BTW_RATE)
    return round(pump_price, 3)


def compute_lag_adjusted_price(prices_asc, eurusd):
    """
    Use the Brent price from LAG_RISING or LAG_FALLING days ago
    to estimate what pump price should be today.
    """
    n = len(prices_asc)
    if n < 2:
        return None, None

    current_brent = prices_asc[-1]["brent"]
    week_ago_brent = prices_asc[max(0, n - 6)]["brent"]
    direction = "rising" if current_brent >= week_ago_brent else "falling"
    lag = LAG_RISING if direction == "rising" else LAG_FALLING

    # Use brent from `lag` days ago as what's currently being priced in
    lag_idx = max(0, n - 1 - lag)
    lagged_brent = prices_asc[lag_idx]["brent"]

    predicted_pump = brent_to_nl_pump(lagged_brent, eurusd)
    return predicted_pump, direction


def build_forecast(prices_asc, eurusd, base_gla=None):
    """
    Build a realistic 5-day GLA forecast.

    Instead of naive linear extrapolation (which blows up), we use:
    1. Short-term momentum: weighted avg of last 3 days Brent change
    2. Mean reversion: pull Brent back toward 30-day moving average
    3. Cap: max realistic daily GLA change is ±5 cent/day (historically ~2–3ct normal)
    4. Anchor: if real GLA known, forecast is expressed as delta from that

    This keeps forecasts in the range drivers actually experience.
    """
    if len(prices_asc) < 10:
        return []

    brent_values = [p["brent"] for p in prices_asc]
    current      = brent_values[-1]
    ma30         = sum(brent_values[-30:]) / min(30, len(brent_values))

    # Momentum: weighted recent daily changes (more weight on last day)
    d1 = brent_values[-1] - brent_values[-2]
    d2 = brent_values[-2] - brent_values[-3]
    d3 = brent_values[-3] - brent_values[-4]
    momentum = (d1 * 0.6) + (d2 * 0.3) + (d3 * 0.1)

    # Mean reversion pull (gentle — 3% per day toward 30d MA)
    REVERSION_RATE = 0.03

    # Max realistic GLA change per day in euros
    MAX_GLA_CHANGE_PER_DAY = 0.07  # €0.07 = 7 cent/day cap (historisch max ~8ct)

    # Today's pump for anchoring
    today_pump = base_gla if base_gla else brent_to_nl_pump(current, eurusd)

    forecast   = []
    brent_proj = current

    for days_ahead in range(1, 6):
        # Apply momentum + mean reversion to Brent
        reversion_pull = (ma30 - brent_proj) * REVERSION_RATE
        daily_brent_change = momentum + reversion_pull
        # Decay momentum each day (trends fade, but not too fast)
        momentum *= 0.88
        brent_proj = brent_proj + daily_brent_change

        # Convert to GLA
        raw_pump = brent_to_nl_pump(brent_proj, eurusd)

        # Anchor delta to today's real GLA if available
        if base_gla:
            model_today = brent_to_nl_pump(current, eurusd)
            delta = raw_pump - model_today
            # Cap the cumulative delta: max ±5ct per day × days_ahead
            max_delta = MAX_GLA_CHANGE_PER_DAY * days_ahead
            delta = max(min(delta, max_delta), -max_delta)
            pump_proj = base_gla + delta
        else:
            # Cap daily change vs previous day
            prev = forecast[-1]["pump_proj"] if forecast else today_pump
            change = raw_pump - brent_to_nl_pump(
                brent_values[-1] + (daily_brent_change * (days_ahead - 1)), eurusd
            )
            change = max(min(change, MAX_GLA_CHANGE_PER_DAY), -MAX_GLA_CHANGE_PER_DAY)
            pump_proj = prev + change

        date_label = (
            datetime.now(timezone.utc).date() + timedelta(days=days_ahead)
        ).isoformat()

        forecast.append({
            "date":       date_label,
            "brent_proj": round(brent_proj, 2),
            "pump_proj":  round(pump_proj, 3),
        })

    return forecast


def get_oil_nieuws():
    """
    Haal de laatste olienieuws-kop op uit gratis RSS feeds.
    Probeert meerdere bronnen en geeft de meest recente relevante kop terug.
    Geeft None terug als alles mislukt — nooit een crash.
    """
    # RSS feeds: Reuters commodities, EIA, OilPrice.com
    feeds = [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.eia.gov/rss/news.xml",
        "https://oilprice.com/rss/main",
    ]

    # Zoekwoorden die relevant zijn voor de NL pompprijs
    RELEVANTE_WOORDEN = [
        "oil", "brent", "crude", "opec", "petroleum", "fuel", "gasoline",
        "benzine", "olieprijs", "hormuz", "iran", "saoedi", "energy",
        "barrel", "refinery", "pump price",
    ]

    beste_kop = None
    beste_datum = None

    for feed_url in feeds:
        try:
            raw = fetch_url(feed_url, timeout=8)

            # Eenvoudige RSS parser — geen externe bibliotheek nodig
            items = re.findall(r'<item>(.*?)</item>', raw, re.DOTALL)
            for item in items[:15]:  # alleen eerste 15 items
                # Titel ophalen
                titel_match = re.search(r'<title[^>]*><!\[CDATA\[(.*?)\]\]></title>', item, re.DOTALL)
                if not titel_match:
                    titel_match = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
                if not titel_match:
                    continue
                titel = re.sub(r'<[^>]+>', '', titel_match.group(1)).strip()

                # Datum ophalen
                datum_match = re.search(r'<pubDate>(.*?)</pubDate>', item)
                datum_str = datum_match.group(1).strip() if datum_match else ""

                # Check of het relevant is voor olie/brandstof
                titel_lower = titel.lower()
                if not any(w in titel_lower for w in RELEVANTE_WOORDEN):
                    continue

                # Datum parsen voor sortering (meest recent winnen)
                try:
                    from email.utils import parsedate_to_datetime
                    datum = parsedate_to_datetime(datum_str)
                except Exception:
                    datum = datetime.now(timezone.utc)

                if beste_datum is None or datum > beste_datum:
                    beste_kop = titel
                    beste_datum = datum

        except Exception as e:
            print(f"RSS feed mislukt ({feed_url}): {e}")
            continue

    if beste_kop:
        # Datum formatteren als "ma 30 mrt"
        if beste_datum:
            dagen = ['ma','di','wo','do','vr','za','zo']
            maanden = ['jan','feb','mrt','apr','mei','jun',
                       'jul','aug','sep','okt','nov','dec']
            try:
                dag_str = f"{dagen[beste_datum.weekday()]} {beste_datum.day} {maanden[beste_datum.month-1]}"
            except Exception:
                dag_str = ""
        else:
            dag_str = ""

        print(f"Nieuws gevonden: {beste_kop} ({dag_str})")
        return {"kop": beste_kop, "datum": dag_str}

    print("Geen relevant olienieuws gevonden.")
    return None


def signal(predicted_today, forecast, nieuws=None):
    """Derive the JA/NEE/MAAKT NIET UIT signal, optionally with news context."""
    if not forecast:
        return "onbekend", "Geen forecastdata beschikbaar."

    pump_in_5d = forecast[-1]["pump_proj"]
    diff = pump_in_5d - predicted_today

    # Nieuws-context toevoegen als suffix
    nieuws_suffix = ""
    if nieuws and nieuws.get("kop"):
        datum = f" ({nieuws['datum']})" if nieuws.get("datum") else ""
        nieuws_suffix = f" Achtergrond: {nieuws['kop']}{datum}."

    pct = round((diff / predicted_today) * 100, 1)
    pct_str = f"+{pct}%" if pct > 0 else f"{pct}%"

    # Gebruik dag 3 ipv dag 5 als beslismoment
    pump_in_3d = forecast[2]["pump_proj"] if len(forecast) >= 3 else pump_in_5d
    diff_3d = pump_in_3d - predicted_today

    if diff_3d > 0.01:
        return "ja", f"De pompprijs wordt naar verwachting {pct_str} hoger de komende dagen."
    elif diff_3d < -0.01:
        return "nee", f"De pompprijs wordt naar verwachting {pct_str} lager de komende dagen."
    else:
        return "maakt_niet_uit", "De pompprijs blijft naar verwachting stabiel de komende dagen."


def main():
    print("Fetching Brent prices from FRED...")
    brent_series = get_brent_series(days_back=40)

    # Fallback: als FRED faalt probeer met minder dagen
    if len(brent_series) < 7:
        print("Retrying FRED with fewer days...")
        brent_series = get_brent_series(days_back=60)

    if len(brent_series) < 7:
        print("FRED failed — using hardcoded fallback Brent price")
        # Gebruik laatste bekende prijs als fallback
        today = datetime.now(timezone.utc).date()
        brent_series = [
            {"date": str(today - timedelta(days=i)), "brent": 74.0}
            for i in range(14, 0, -1)
        ]

    print(f"Got {len(brent_series)} Brent data points")
    current_brent = brent_series[-1]["brent"]
    print(f"Latest Brent: ${current_brent:.2f} on {brent_series[-1]['date']}")

    print("Fetching EUR/USD from ECB...")
    try:
        eurusd = get_eurusd()
        print(f"EUR/USD: {eurusd:.4f}")
    except Exception as e:
        print(f"ECB EUR/USD failed: {e} — using fallback 1.08")
        eurusd = 1.08

    # ── Cross-check: scrape real GLA from brandstof-zoeker.nl ────────────────
    print("Scraping GLA cross-check from brandstof-zoeker.nl...")
    try:
        gla_data = get_gla_brandstofzoeker()
    except Exception as e:
        print(f"GLA scrape failed: {e}")
        gla_data = None

    # ── Model prediction (lag-adjusted) ──────────────────────────────────────
    predicted_gla, direction = compute_lag_adjusted_price(brent_series, eurusd)
    print(f"Model predicted GLA: €{predicted_gla:.3f}/L (direction: {direction})")

    # ── Calibrate: if real GLA available, use it as anchor for today ─────────
    if gla_data and gla_data.get("gla"):
        real_gla = gla_data["gla"]
        model_error = round(predicted_gla - real_gla, 3)
        print(f"Real GLA: €{real_gla:.3f} | Model error: {model_error:+.3f}")
        base_gla = real_gla
    else:
        model_error = None
        base_gla = predicted_gla
        print("No cross-check available — using model prediction as base")

    # ── 5-day forecast — anchored to real GLA, capped at realistic changes ───
    forecast = build_forecast(brent_series, eurusd, base_gla=base_gla)

    # ── Nieuws: laatste relevante olie-kop ophalen voor context ──────────────
    print("Ophalen olienieuws van RSS feeds...")
    try:
        nieuws = get_oil_nieuws()
    except Exception as e:
        print(f"Nieuws ophalen mislukt: {e}")
        nieuws = None

    # ── Signal: based on forecast vs today's real GLA ────────────────────────
    advies, reden = signal(base_gla, forecast, nieuws=nieuws)
    print(f"Signal: {advies} | {reden}")

    # Price change this week (Brent)
    week_ago_brent = brent_series[max(0, len(brent_series) - 6)]["brent"]
    brent_change_pct = round(((current_brent - week_ago_brent) / week_ago_brent) * 100, 1)

    # Pump price user sees = GLA minus average station discount
    pump_voor_gebruiker = round(base_gla - STATION_KORTING, 2)

    # Build output JSON
    output = {
        "updated":              datetime.now(timezone.utc).isoformat(),
        "brent_usd":            round(current_brent, 2),
        "brent_date":           brent_series[-1]["date"],
        "brent_change_pct":     brent_change_pct,
        "eurusd":               round(eurusd, 4),
        "predicted_pump":       base_gla,        # GLA (used for breakdown display)
        "pump_voor_gebruiker":  pump_voor_gebruiker,  # GLA minus korting
        "station_korting":      STATION_KORTING,
        "direction":            direction,
        "advies":               advies,
        "reden":                reden,
        "nieuws":               nieuws,
        "forecast":             forecast,
        "cross_check": {
            "gla_vandaag":      gla_data.get("gla") if gla_data else None,
            "model_error":      model_error,
            "bron":             gla_data.get("source") if gla_data else None,
            "accijns_werkelijk": gla_data.get("accijns") if gla_data else None,
            "btw_werkelijk":     gla_data.get("btw") if gla_data else None,
            "oliemij_werkelijk": gla_data.get("oliemij") if gla_data else None,
        },
        "breakdown": {
            "accijns":   ACCIJNS_PER_LITRE,
            "cova":      COVA_PER_LITRE,
            "btw_pct":   BTW_RATE * 100,
            "marge":     MARGE,
        }
    }

    with open("prediction.json", "w") as f:
        json.dump(output, f, indent=2)

    print("Written to prediction.json")

    # ── Diesel prediction ─────────────────────────────────────────────────────
    # Diesel GLA from brandstof-zoeker.nl (scrape diesel page)
    DIESEL_ACCIJNS   = 0.5523
    DIESEL_KORTING   = 0.20
    DIESEL_MARGE     = 0.18
    DIESEL_REFINING  = 0.065  # iets hogere raffinagekosten voor diesel

    def brent_to_diesel_gla(brent_usd, eur_usd):
        crude_eur  = (brent_usd / BARREL_TO_LITRES) / eur_usd
        product    = crude_eur / REFINING_YIELD + DIESEL_REFINING
        pre_btw    = product + DIESEL_ACCIJNS + COVA_PER_LITRE + DIESEL_MARGE
        return round(pre_btw * (1 + BTW_RATE), 3)

    # Scrape diesel GLA
    try:
        diesel_html = fetch_url("https://www.brandstof-zoeker.nl/adviesprijzen/diesel/")
        diesel_match = re.search(r'benzineprijs op .+? is\s*[€]\s*([\d,]+)', diesel_html)
        diesel_gla = float(diesel_match.group(1).replace(',', '.')) if diesel_match else brent_to_diesel_gla(current_brent, eurusd)
        print(f"Diesel GLA: €{diesel_gla:.3f}")
    except Exception as e:
        print(f"Diesel scrape failed: {e}")
        diesel_gla = brent_to_diesel_gla(current_brent, eurusd)

    # Diesel forecast
    diesel_forecast = []
    for f in forecast:
        delta = f["pump_proj"] - base_gla
        diesel_forecast.append({
            "date":       f["date"],
            "brent_proj": f["brent_proj"],
            "pump_proj":  round(diesel_gla + delta, 3),
        })

    # Diesel signal
    diesel_diff_3d = diesel_forecast[2]["pump_proj"] - diesel_gla if len(diesel_forecast) >= 3 else 0
    diesel_pct     = round((diesel_diff_3d / diesel_gla) * 100, 1) if diesel_gla else 0
    if diesel_diff_3d > 0.01:
        diesel_advies = "ja"
        diesel_reden  = f"De dieselprijs wordt naar verwachting +{diesel_pct}% hoger de komende dagen."
    elif diesel_diff_3d < -0.01:
        diesel_advies = "nee"
        diesel_reden  = f"De dieselprijs wordt naar verwachting {diesel_pct}% lager de komende dagen."
    else:
        diesel_advies = "maakt_niet_uit"
        diesel_reden  = "De dieselprijs blijft naar verwachting stabiel de komende dagen."

    diesel_output = {
        "updated":             datetime.now(timezone.utc).isoformat(),
        "predicted_pump":      diesel_gla,
        "pump_voor_gebruiker": round(diesel_gla - DIESEL_KORTING, 2),
        "station_korting":     DIESEL_KORTING,
        "direction":           direction,
        "advies":              diesel_advies,
        "reden":               diesel_reden,
        "nieuws":              None,
        "forecast":            diesel_forecast,
        "breakdown": {
            "accijns": DIESEL_ACCIJNS,
            "cova":    COVA_PER_LITRE,
            "btw_pct": BTW_RATE * 100,
            "marge":   DIESEL_MARGE,
        }
    }

    with open("prediction_diesel.json", "w") as f:
        json.dump(diesel_output, f, indent=2)

    print("Written to prediction_diesel.json")
    return output


if __name__ == "__main__":
    main()
