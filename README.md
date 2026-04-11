# vandaagtanken.nl — Moet ik vandaag tanken?

> Eén vraag. Eén antwoord. Elke dag.

vandaagtanken.nl vertelt Nederlandse automobilisten dagelijks of ze nu moeten tanken of beter kunnen wachten — op basis van de actuele benzineprijs en een verwachting van de komende dagen.

---

## Hoe het werkt

Het systeem bestaat uit drie onderdelen:

1. **`predict.py`** — haalt elke ochtend de actuele GLA op en berekent de verwachting
2. **`.github/workflows/predict.yml`** — GitHub Actions draait dit script dagelijks gratis
3. **`index.html`** — de site leest `prediction.json` en toont het advies

### Prijsberekening

De getoonde prijs is de officiële Gemiddelde Landelijke Adviesprijs (GLA) minus €0,25 gemiddelde korting:

```
Pompprijs = GLA − €0,25 korting
```

De GLA wordt dagelijks opgehaald van brandstof-zoeker.nl — dezelfde bron als UnitedConsumers. Dit is de officiële adviesprijs op basis van de vijf grootste oliemaatschappijen in Nederland.

### Forecast

De verwachting voor de komende 5 dagen is gebaseerd op:

- **Brent ruwe olieprijs** (dagelijks via FRED, St. Louis Fed)
- **Historische gevoeligheid**: ~€0,017 GLA-verandering per $10 Brent-beweging
- **Momentum decay**: Brent-momentum neemt dagelijks af met 20%

Het advies (JA / NEE / MAAKT NIET UIT) is gebaseerd op de verwachte prijs over 3 dagen.

### Databronnen

| Bron | Data |
|---|---|
| brandstof-zoeker.nl | GLA benzine en diesel (dagelijks) |
| FRED (St. Louis Fed) | Brent olieprijs (dagelijks) |

---

## Setup

### 1. GitHub Pages aanzetten
`Settings → Pages → Source: Deploy from branch → main → / (root)`

### 2. Domein koppelen (optioneel)
`Settings → Pages → Custom domain → vul je domein in`

Voeg deze DNS records toe bij je domeinregistrar:

```
@    A    185.199.108.153
@    A    185.199.109.153
@    A    185.199.110.153
@    A    185.199.111.153
www  CNAME jouwgebruikersnaam.github.io
```

### 3. Klaar!
GitHub Actions draait `predict.py` elke ochtend om 09:00 NL tijd en schrijft `prediction.json` en `prediction_diesel.json` weg. De site laadt deze bestanden automatisch.

### Handmatig draaien
`Actions → Daily Petrol Price Prediction → Run workflow`

---

## Lokaal testen

```bash
pip install requests
python predict.py          # genereert prediction.json en prediction_diesel.json
python -m http.server 8000 # open http://localhost:8000
```

---

## Kosten

| Onderdeel | Kosten |
|---|---|
| GitHub repo + Actions | €0 |
| GitHub Pages hosting | €0 |
| Databronnen | €0 |
| Domeinnaam | ~€10/jaar |
| **Totaal** | **~€10/jaar** |

---

## Affiliate

De site toont een link naar de UnitedConsumers tankpas via het Awin affiliate netwerk. UnitedConsumers geeft minimaal 6 cent korting per liter bij 600+ tankstations in Nederland.
