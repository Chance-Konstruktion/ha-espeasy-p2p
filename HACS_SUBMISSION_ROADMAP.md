# HACS Submission Roadmap – ha-espeasy-p2p

Brand-Assets liegen im Repo unter `brand/`.

Forks bereit:
- Brands-Fork:  https://github.com/Chance-Konstruktion/brands
- Default-Fork: https://github.com/Chance-Konstruktion/default

Ziel-Repo: https://github.com/chance-konstruktion/ha-espeasy-p2p

---

## Schritt 1 — GitHub-Repo-Settings setzen (1 Minute)

Auf https://github.com/chance-konstruktion/ha-espeasy-p2p oben rechts auf das ⚙️ neben "About" klicken:

- **Description**: `Local-push ESPEasy/RPiEasy integration for Home Assistant via the native C013 UDP protocol`
- **Website**: leer lassen oder GitHub-Pages
- **Topics**: `home-assistant` `hacs` `integration` `espeasy` `rpieasy` `home-automation` `udp` `iot`
- Häkchen bei **Releases** (rein kosmetisch für die About-Sidebar — HACS liest die Releases via API ohnehin)

---

## Schritt 2 — Brands-PR (zuerst! ohne Brand kein HACS-Default-Merge)

### 2.1 Branch im Fork anlegen
```bash
git clone https://github.com/Chance-Konstruktion/brands.git
cd brands
git remote add upstream https://github.com/home-assistant/brands.git
git fetch upstream
# home-assistant/brands nutzt 'master' als Default-Branch
git checkout -b add-espeasy-p2p upstream/master
```

### 2.2 Brand-Dateien einkopieren
Aus `ha-espeasy-p2p/brand/` in den brands-Klon kopieren. Der Ordnername unter
`custom_integrations/` MUSS exakt dem `domain`-Wert aus `manifest.json`
entsprechen (hier: `espeasy_p2p`):
```bash
mkdir -p custom_integrations/espeasy_p2p
cp ../ha-espeasy-p2p/brand/icon.png       custom_integrations/espeasy_p2p/icon.png
cp ../ha-espeasy-p2p/brand/icon@2x.png    custom_integrations/espeasy_p2p/icon@2x.png
cp ../ha-espeasy-p2p/brand/logo.png       custom_integrations/espeasy_p2p/logo.png
cp ../ha-espeasy-p2p/brand/logo@2x.png    custom_integrations/espeasy_p2p/logo@2x.png
```

Bildgrößen-Spec (vom Brands-Bot strikt geprüft):
- `icon.png` **exakt** 256×256
- `icon@2x.png` **exakt** 512×512
- `logo.png` Höhe **max.** 128 (Breite frei, proportional)
- `logo@2x.png` Höhe **max.** 256 (Breite frei, proportional)

### 2.3 Commit + Push
```bash
git add custom_integrations/espeasy_p2p
git commit -m "Add espeasy_p2p"
git push -u origin add-espeasy-p2p
```

### 2.4 PR öffnen
- URL: https://github.com/home-assistant/brands/compare/master...Chance-Konstruktion:brands:add-espeasy-p2p
- **Title**: `Add espeasy_p2p`
- **Body**: kurz: *"Logo/icons for the ESPEasy P2P custom integration (chance-konstruktion/ha-espeasy-p2p)."*
- Auf den Bot warten (validiert Größe/Format) → meist wenige Tage bis Merge.

---

## Schritt 3 — HACS-Default-PR (erst NACH Brands-Merge!)

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
Den Slug `chance-konstruktion/ha-espeasy-p2p` alphabetisch einsortieren. Der Bot
prüft:
- gültige JSON-Syntax (keine trailing comma!)
- strikt alphabetische Sortierung (case-sensitive, ASCII)

### 3.3 Commit + Push
```bash
git add integration
git commit -m "Add chance-konstruktion/ha-espeasy-p2p"
git push -u origin add-espeasy-p2p
```

### 3.4 PR öffnen
- URL: https://github.com/hacs/default/compare/main...Chance-Konstruktion:default:add-espeasy-p2p
- **Title**: `Add chance-konstruktion/ha-espeasy-p2p`
- **Body**: leer lassen oder kurz beschreiben — der HACS-Bot prüft automatisch:
  - hacs.json + manifest.json
  - mind. ein GitHub-Release, dessen Inhalt `custom_components/<domain>/`
    enthält (entweder im Repo-Root oder als ZIP-Asset) — sonst schlägt die
    Validierung fehl, obwohl Releases vorhanden sind
  - Brand-Eintrag (deshalb Schritt 2 zuerst!)
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
- [x] Brand-Assets in `brand/` (256/512 Icon, 128/256 Logo)
- [x] 2 GitHub-Releases vorhanden
- [ ] GitHub-Repo Topics + About gesetzt → **Schritt 1**
- [ ] Brands-PR offen/gemerged → **Schritt 2**
- [ ] HACS-Default-PR offen/gemerged → **Schritt 3**

---

## Wenn etwas schiefläuft

- **Bot meckert in Brands-PR über Bildgröße** → Pillow-Skript erneut laufen lassen oder
  manuell auf exakt 256×256 / 512×512 (Icon) bzw. max 128/256 Höhe (Logo) bringen.
- **HACS-Bot meckert "no brand"** → Schritt 2 noch nicht gemerged. Warten.
- **HACS-Bot meckert "no release"** oder "release does not contain integration" →
  Release prüfen: muss `custom_components/<domain>/` enthalten (Repo-Layout oder
  ZIP-Asset).
- **hassfest fail** → Output lesen, meistens Translations-Quotes oder fehlende Keys.
