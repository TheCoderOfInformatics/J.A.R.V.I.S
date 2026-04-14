# JARVIS — Just A Rather Very Intelligent System

Ein autonomer KI-Assistent im Stil von Tony Starks JARVIS aus Iron Man.  
Läuft unsichtbar im Hintergrund, reagiert auf **Doppelklatschen**, und kann sich **selbst weiterentwickeln**.

Powered by **Llama 4 Scout** (Groq — kostenlos) und **Whisper** (Groq STT).

---

## Features

### Aktivierung
- **Doppelklatschen** → JARVIS erwacht, Back-in-Black-Riff spielt, Hologramm startet
- **Wake-Word** "Hey Jarvis" wenn aktiv
- **Stimmverifizierung** — reagiert nur auf den registrierten Nutzer

### Holographic Display
- 3D-Hologramm im Three.js mit Post-Processing (Bloom, FogExp2, dynamische Beleuchtung)
- Echtzeit-Audio-Level-Visualisierung (28 Bars)
- Vollbild mit CRT-Scanlines und Scan-Sweep
- **Netzwerk-Zugriff**: Von jedem Gerät im WLAN (Handy, Tablet, PC) per Browser erreichbar

### Autonomie — 65 Tools
- **System**: PowerShell, Dateien, Prozesse, Registry, WMI, .NET
- **Internet**: Websuche, News, Wetter, Download, Standort
- **Kommunikation**: E-Mail (Outlook), Benachrichtigungen, WiFi
- **Medien**: Lautstärke, Musik (Spotify/YouTube), Helligkeit
- **Smart Home**: Philips Hue, MQTT, generische REST-APIs
- **Sicherheit**: Firewall-Check, Port-Scan, Prozess-Analyse
- **Wissen**: Übersetzen, Wörterbuch, Einheiten/Währung, Aktien/Crypto
- **Produktivität**: Notizen, Timer, Wecker, Kontakte, Kalender
- **Fortgeschritten**: OCR, Code-Generierung, Deep Research, System-Diagnose

### Selbstverbesserung
- `read_own_code` / `edit_own_code` — JARVIS liest und editiert seinen eigenen Code
- `add_new_tool` — erstellt neue Tools zur Laufzeit
- `learn` — speichert Erkenntnisse permanent (werden bei jedem Start geladen)
- `self_reflect` — regelmäßige Selbstanalyse alle 60 Min.
- **Backup + Syntax-Check + Auto-Rollback** bei jeder Code-Änderung

### Proaktivität
- **Morgen-Briefing** (7–10 Uhr): Wetter, Termine, Aufgaben
- **System-Überwachung** alle 5 Min. (CPU, RAM, Akku, Festplatte)
- **Kontext-Check** alle 10 Min. (silent notifications)
- **Geplante Tasks** via `schedule_task`

### Autostart
- VBS-Skript im Windows-Autostart-Ordner
- Unsichtbarer Hintergrund-Prozess via `pythonw`
- System-Tray-Icon mit Menü

---

## Installation

```bash
git clone https://github.com/TheCoderOfInformatics/Jarvis-da-Vinci.git
cd Jarvis-da-Vinci
pip install -r requirements.txt
```

Erstelle `.env`:
```
GROQ_API_KEY=gsk_...
```
(API-Key kostenlos auf [console.groq.com](https://console.groq.com))

---

## Nutzung

### Erststart (Stimmregistrierung)
```bash
python Main.py enroll
```

### Normal starten
```bash
python Main.py          # Wake-Word + Klatschen
python Main.py text     # Text-Modus
python Main.py voice    # Sprach-Modus ohne Wake-Word
```

### Autostart einrichten
Kopiere `JARVIS.vbs` nach:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

---

## Architektur

```
Main.py              ← KI-Kernel, STT, TTS, Wake-Word, Automation-Loop
tools.py             ← 65 Tools (Definitionen + Implementierungen)
hologram.py          ← HTTP-Server, Remote-API, Tray-Icon
hologram.html        ← Three.js 3D-Visualisierung, Chat-UI
data/
  sessions/          ← Gesprächsverlauf
  learnings.json     ← Permanente Erkenntnisse
  contacts.json      ← Kontaktdatenbank
  knowledge.json     ← Wissensspeicher
backups/             ← Auto-Backups bei Code-Änderungen
```

---

## Technologie

- **LLM**: Llama 4 Scout 17B (Groq, kostenlos)
- **STT**: Whisper Large v3 Turbo (Groq)
- **TTS**: Microsoft Edge TTS (Neural, deutsch)
- **Audio**: sounddevice + pygame
- **Hologramm**: Three.js mit EffectComposer + UnrealBloomPass
- **Server**: Python `http.server` mit REST-API + CORS

---

## Charakter

JARVIS spricht britisch-formal mit trockenem Humor. Immer "Sir". Nie Floskeln.  
Maximal 2 Sätze, ausschließlich Deutsch. Handelt ohne Rückfrage.

---

## Lizenz

MIT
