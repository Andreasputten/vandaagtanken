# tanknu.nl — Nederlandse benzineprijs voorspeller

> Moet ik nu tanken? Eén vraag. Één antwoord.

## Hoe het werkt

Het systeem bestaat uit drie onderdelen:

1. **`predict.py`** — Python script dat dagelijks de verwachte NL pompprijs berekent
2. **`.github/workflows/predict.yml`** — GitHub Actions draait dit script elke ochtend gratis
3. **`index.html`** — De website leest `prediction.json` en toont het advies

### Prijsformule

De NL pompprijs bestaat uit bekende, vaste componenten:

```
Pompprijs = (product_prijs + accijns + COVA + marge) × 1.21 BTW

product_prijs = (Brent $/barrel ÷ 158.987 liter ÷ EUR/USD) ÷ 0.445 yield + raffinagekosten
accijns       = €0.8447/L (2026, Euro 95)
COVA heffing  = €0.0085/L
marge         = €0.185/L
BTW           = 21%
```

### Data bronnen (gratis, geen API key)

| Bron | Data | URL |
|---|---|---|
| FRED (St. Louis Fed) | Brent $/barrel dagelijks | `fred.stlouisfed.org` |
| ECB Data Portal | EUR/USD dagelijks | `data-api.ecb.europa.eu` |

### Vertraging (lag)

Brent prijswijzigingen bereiken de Nederlandse pomp na:
- **4 dagen** bij prijsstijgingen (stations passen snel aan)
- **6 dagen** bij prijsdalingen (asymmetrisch — bekend fenomeen)

---

## Setup (5 minuten)

### 1. Fork deze repository op GitHub

### 2. Zet GitHub Pages aan
`Settings → Pages → Source: Deploy from branch → main → / (root)`

### 3. Klaar!
- GitHub Actions draait `predict.py` elke ochtend om 09:00 NL tijd
- Resultaat wordt opgeslagen in `prediction.json`
- `index.html` leest dit bestand automatisch
- Koppel je eigen domein via `Settings → Pages → Custom domain`

### Handmatig draaien
`Actions → Daily Petrol Price Prediction → Run workflow`

---

## Lokaal testen

```bash
python predict.py          # genereert prediction.json
python -m http.server 8000 # open http://localhost:8000
```

---

## Kosten

| Onderdeel | Kosten |
|---|---|
| GitHub repo + Actions | €0 |
| GitHub Pages hosting | €0 |
| FRED API | €0 |
| ECB API | €0 |
| Domeinnaam (tanknu.nl) | ~€10/jaar |
| **Totaal** | **~€10/jaar** |
