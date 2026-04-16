# JARVIS v2.0

Eine verbesserte Version von Jarvis mit moderner Architektur, persistenter Aufgabenplanung und proaktiver Hintergrundüberwachung.

## Highlights

- Modularer Aufbau: `kernel`, `tools`, `scheduler`, `config`.
- Eigenständige Background-Tasks: Systemchecks, Morgen-Briefing, Abend-Zusammenfassung.
- Persistente Notizen und geplante Aufgaben via JSON.
- Deutsche Ausgabe, keine unnötigen Rückfragen, klare kurze Antworten.
- Agentischer Tool-Use-Loop mit selbstständiger Tool-Auswahl.

## Installation

```bash
cd Jarvis-da-Vinci
pip install -r requirements.txt
```

## Start

- Interaktiver Textmodus:
  ```bash
  python -m jarvis_v2 text
  ```
- Hintergrundmodus mit Proaktivität:
  ```bash
  python -m jarvis_v2 daemon
  ```

## Aufgaben planen

- Neue Aufgabe anlegen:
  ```bash
  python -m jarvis_v2 task add "Systemcheck" 30 "get_system_info"
  ```
- Aufgaben auflisten:
  ```bash
  python -m jarvis_v2 task list
  ```
- Aufgabe löschen:
  ```bash
  python -m jarvis_v2 task cancel "Systemcheck"
  ```

## Daten

- `data/notes.json`
- `data/tasks.json`
- `data/learnings.json`
- `data/sessions/`

## Verbesserungen gegenüber Jarvis 1

- Sauberere Trennung von Kernlogik und Tools.
- Hintergrundagent läuft als eigener Thread.
- Persistente Planung und lokale Gedächtnisspeicherung.
- Bessere Struktur für zukünftige Erweiterungen.
