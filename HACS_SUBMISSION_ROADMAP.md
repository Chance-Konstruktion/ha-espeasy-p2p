# HACS Submission Roadmap – ha-espeasy-p2p

Brand-Assets liegen im Repo unter `custom_components/espeasy_p2p/brand/`
(neue HA-2026.3+ Konvention; siehe Schritt 2).

Ziel-Repo: https://github.com/chance-konstruktion/ha-espeasy-p2p

Default-Fork: https://github.com/Chance-Konstruktion/default

---

## Schritt 1 — GitHub-Repo-Settings setzen (1 Minute)

Auf https://github.com/chance-konstruktion/ha-espeasy-p2p oben rechts auf das
⚙️ neben "About" klicken:

- **Description**: `Local-push ESPEasy/RPiEasy integration for Home Assistant via the native C013 UDP protocol`
- **Website**: leer lassen oder GitHub-Pages
- **Topics**: `home-assistant` `hacs` `integration` `espeasy` `rpieasy` `home-automation` `udp` `iot`
- Häkchen bei **Releases** (rein kosmetisch für die About-Sidebar — HACS liest die Releases via API ohnehin)

---

## Schritt 2 — Brand-Assets im Integration-Repo (statt Brands-PR!)

Seit Home Assistant **2026.3.0** akzeptiert `home-assistant/brands` keine
PRs für Custom Integrations mehr. Brand-Icons werden direkt aus dem
Integration-Repo geladen — kein separater PR notwendig.

**Pflichtpfad**: `custom_components/<domain>/brand/`

Für dieses Repo also: `custom_components/espeasy_p2p/brand/` mit:
- `icon.png` — exakt 256×256 (1:1)
- `icon@2x.png` — exakt 512×512 (1:1)
- `logo.png` — Höhe max. 128, Breite frei
- `logo@2x.png` — Höhe max. 256, Breite frei

Optional zusätzlich: `dark_icon.png`, `dark_icon@2x.png`, `dark_logo.png`,
`dark_logo@2x.png` für Dark-Mode-Varianten.

HA serviert sie über `/api/brands/integration/<domain>/<file>`; lokal
abgelegte Assets haben Vorrang vor dem CDN.

Referenz:
[Brands Proxy API Announcement (24.02.2026)](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).

---

## Schritt 3 — HACS-Default-PR

### 3.1 Branch im Default-Fork anlegen
```bash
git clone https://github.com/Chance-Konstruktion/default.git
cd default
git remote add upstream https://github.com/hacs/default.git
git fetch upstream
git checkout -b add-espeasy-p2p upstream/main
```

### 3.2 Datei `integration` editieren
Die Datei heißt schlicht `integration` (ohne Endung), Inhalt ist eine JSON-Liste.
Den Slug `Chance-Konstruktion/ha-espeasy-p2p` alphabetisch (case-insensitive)
einsortieren. Der Bot prüft:
- gültige JSON-Syntax (keine trailing comma!)
- alphabetische Sortierung

### 3.3 Commit + Push
```bash
git add integration
git commit -m "Add Chance-Konstruktion/ha-espeasy-p2p"
git push -u origin add-espeasy-p2p
```

### 3.4 PR öffnen
- URL: https://github.com/hacs/default/compare/main...Chance-Konstruktion:default:add-espeasy-p2p
- **Title**: `Add Chance-Konstruktion/ha-espeasy-p2p`
- **Body**: leer lassen oder kurz beschreiben — der HACS-Bot prüft automatisch:
  - hacs.json + manifest.json
  - mind. ein GitHub-Release, dessen Inhalt `custom_components/<domain>/`
    enthält (entweder im Repo-Root oder als ZIP-Asset) — sonst schlägt die
    Validierung fehl, obwohl Releases vorhanden sind
  - README, Topics, Description

Review-Wartezeit oft 1–4 Wochen.

---

## manifest.json — Pflicht-Keys für HACS-Default

Für die Aufnahme in HACS-Default zwingend vorhanden:

- [x] `domain`
- [x] `name`
- [x] `documentation` (URL)
- [x] `issue_tracker` (URL)
- [x] `codeowners` (Liste mit mind. einem GitHub-Handle)
- [x] `version` (bei custom integrations zwingend, bei Core-Integrationen nicht)
- [x] `requirements` (auch wenn leere Liste)

---

## Aktueller Repo-Zustand (Checkliste)

- [x] `custom_components/espeasy_p2p/` Struktur
- [x] `manifest.json` mit allen Pflicht-Keys (siehe Abschnitt oben)
- [x] `hacs.json`
- [x] `info.md`
- [x] README + LICENSE + CHANGELOG
- [x] DE/EN Translations
- [x] config_flow + options_flow (inkl. Nachkommastellen)
- [x] `.github/workflows/hassfest.yaml`
- [x] `.github/workflows/hacs.yaml`
- [x] Tests-Skeleton
- [x] Brand-Assets in `custom_components/espeasy_p2p/brand/` (256/512 Icon, 128/256 Logo)
- [x] 2 GitHub-Releases vorhanden
- [ ] GitHub-Repo Topics + About gesetzt → **Schritt 1**
- [ ] HACS-Default-PR offen/gemerged → **Schritt 3**

---

## Wenn etwas schiefläuft

- **HACS-Bot meckert "no release"** oder "release does not contain integration" →
  Release prüfen: muss `custom_components/<domain>/` enthalten (Repo-Layout oder
  ZIP-Asset).
- **hassfest fail** → Output lesen, meistens Translations-Quotes oder fehlende Keys.
- **Icon erscheint nicht in der HA-UI** → Pfad prüfen
  (`custom_components/<domain>/brand/icon.png`) und HA mindestens 2026.3.0
  voraussetzen. Im manifest.json keine extra Config nötig.
