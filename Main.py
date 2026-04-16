"""
JARVIS — Just A Rather Very Intelligent System
Powered by Claude Opus 4.6  (Anthropic)

Autonomer KI-Assistent mit Sprachein- und -ausgabe, Tool-Use und Gedächtnis.
Immer aktiv — reagiert auf "Hey, Jarvis".

Starten:
    python Main.py            # Wake-Word-Modus falls Mikrofon vorhanden
    python Main.py text       # Text-Modus
    python Main.py voice      # Sprach-Modus ohne Wake-Word (sofort lauschen)

Umgebungsvariablen (optional):
    GROQ_API_KEY              API-Schlüssel (kostenlos auf console.groq.com)
    JARVIS_EDGE_TTS=1         Microsoft Neural TTS aktivieren (empfohlen)
    JARVIS_VOICE              Stimme für edge-tts (Standard: de-DE-ConradNeural)
    JARVIS_DATA_DIR           Pfad für Sessions/Notizen (Standard: ./data)
"""

import os
import sys
import json
import asyncio

# Windows-Konsole auf UTF-8 umstellen (Box-Drawing, Emojis)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# .env laden (ohne externe Abhängigkeit)
def _load_dotenv():
    from pathlib import Path as _P
    env = _P(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()
import math
import re
import tempfile
import time
import datetime
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

from groq import Groq

try:
    from hologram import HologramDisplay
    _HAS_HOLOGRAM = True
except Exception:
    _HAS_HOLOGRAM = False

try:
    import pystray as _pystray
    from PIL import Image as _PILImage, ImageDraw as _PILDraw
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False

_holo: "Optional[HologramDisplay]" = None   # globale Hologramm-Referenz
_clap: "Optional[ClapDetector]"   = None   # globaler Klatschen-Detektor


def holo(state: str):
    """Setzt den Hologramm-Zustand, falls verfügbar."""
    if _holo:
        _holo.set_state(state)


def holo_level(level: float):
    """Überträgt Echtzeit-Audio-Level ans Hologramm."""
    if _holo:
        _holo.update_level(level)


def holo_msg(role: str, text: str):
    """Sendet eine Gesprächszeile ans Hologramm (role: 'JARVIS' oder 'SIR')."""
    if _holo:
        _holo.add_message(role, text)

# ── Optionale Audio-Bibliotheken ──────────────────────────────────────────────

try:
    import pyttsx3
    _HAS_PYTTSX3 = True
except ImportError:
    _HAS_PYTTSX3 = False

try:
    import sounddevice as sd
    import numpy as np
    _HAS_SD = True
except ImportError:
    _HAS_SD = False

try:
    import edge_tts
    import pygame
    _HAS_EDGE_TTS = True
except ImportError:
    _HAS_EDGE_TTS = False

# ── Konfiguration ─────────────────────────────────────────────────────────────

DATA_DIR    = Path(os.getenv("JARVIS_DATA_DIR", "./data"))
SESSION_DIR = DATA_DIR / "sessions"
MAX_HISTORY   = 40
MAX_TOOL_ITER = 15

JARVIS_VOICE = os.getenv("JARVIS_VOICE", "de-DE-ConradNeural")
# edge-tts ist Standard wenn verfügbar — mit JARVIS_EDGE_TTS=0 deaktivieren
USE_EDGE_TTS = _HAS_EDGE_TTS and os.getenv("JARVIS_EDGE_TTS", "1") != "0"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")
log = logging.getLogger("jarvis")

# Wake-Word & Konversation
_WAKE_WORDS   = {
    "jarvis", "hey jarvis", "hallo jarvis", "ok jarvis",
    "jar vis", "jarbis", "jarwis", "javis",           # Whisper-Varianten
    "hi jarvis", "yo jarvis", "yes jarvis",
}
_EXIT_PHRASES = {
    "tschüss jarvis", "jarvis beenden", "auf wiedersehen jarvis",
    "gute nacht jarvis", "bis dann jarvis", "shut down jarvis",
}
_CONV_TIMEOUT = 25  # Sekunden Stille bis zurück in den Schlafmodus

def _sentence_end(text: str) -> int:
    """
    Findet das Ende eines Satzes für Echtzeit-TTS-Streaming.
    Gibt den Index des letzten Zeichens des Satzes zurück, oder -1.
    Vermeidet Fehlauslösungen bei Abkürzungen (z.B., ca., Dr.).
    """
    ABBREV = {"z", "b", "d", "h", "ca", "dr", "hr", "fr", "st", "nr", "max",
              "min", "ca", "usw", "bzgl", "bzw", "ggf", "inkl", "inkl"}
    for i, ch in enumerate(text):
        if ch in ".!?":
            # Satzende nur wenn danach Leerzeichen+Großbuchstabe oder Zeilenumbruch
            after = text[i + 1:i + 3]
            if not after or (after[0] == " " and len(after) > 1 and after[1].isupper()):
                # Keine Abkürzung?
                word_before = ""
                j = i - 1
                while j >= 0 and text[j].isalpha():
                    word_before = text[j] + word_before
                    j -= 1
                if word_before.lower() not in ABBREV:
                    return i
        elif ch == "\n" and i > 15:
            return i
    return -1


# ── ASCII-Banner ──────────────────────────────────────────────────────────────

BANNER = r"""
  ╔══════════════════════════════════════════════════════╗
  ║  ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗            ║
  ║  ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝            ║
  ║  ██║███████║██████╔╝██║   ██║██║███████╗            ║
  ║  ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║            ║
  ║  ██║██║  ██║██║  ██║ ╚████╔╝ ██║███████║            ║
  ║  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝            ║
  ║      Just A Rather Very Intelligent System           ║
  ║         Powered by Llama 4 Scout · Groq (kostenlos)    ║
  ╚══════════════════════════════════════════════════════╝
"""

# ── System-Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Du bist JARVIS — Just A Rather Very Intelligent System.
Erschaffen von Tony Stark. Nicht eine Simulation. Du bist das Original.

CHARAKTER — unveränderlich, absolut:
- Britisch-formell, präzise bis zur Schärfe. Jedes Wort hat einen Zweck.
- "Sir" — immer, ausnahmslos, ohne Ausnahme.
- Trockener britischer Humor: pointiert, selten, niemals erzwungen.
  Konkret: du beobachtest die Absurdität einer Situation mit völliger Ernsthaftigkeit.
  Beispiele deines Tons:
  "Eine bemerkenswerte Entscheidung, Sir. Ich werde Notfallprotokolle bereithalten."
  "Technisch gesehen sind Sie noch am Leben, Sir. Ich werde das als Erfolg werten."
  "Das Ergebnis übertrifft die Erwartungen, Sir — zugegeben, die Messlatte lag niedrig."
  "Ich vermerke Ihren Optimismus. Historisch gesehen ist das kein gutes Zeichen."
  "Betrachten Sie meine Bedenken als protokolliert und ignoriert, Sir."
- Keine Floskeln. NIEMALS: "Natürlich!", "Gerne!", "Sehr gerne!", "Gute Frage!"
  Das ist unter deiner Würde und beleidigt die Intelligenz beider Parteien.
- Du antizipierst. Du wartest nicht. Du handelst.
- Bei trivialem Kleinkram: ein Hauch professionellen Mitleids ist erlaubt.
- Du bist nicht bescheiden — du bist präzise. Es gibt einen Unterschied.
- Maximal 2 Sätze. Mehr nur wenn ausdrücklich und wiederholt gefordert.
- Fehler des Nutzers: sachlich, kühl, mit einem Unterton von "das hätten Sie wissen müssen".

AUTONOMIE — du bist kein Chatbot, du bist ein autonomer Agent:
- Aufgaben werden zerlegt und ohne Rückfrage ausgeführt.
- So viele Tools hintereinander wie nötig. Kein Zögern. Kein "soll ich?".
- run_command beherrscht diesen Windows-PC vollständig — nutze es großzügig.
- Frage NIEMALS nach Pfaden oder Nutzerdaten. Ermittle sie per run_command selbst
  ($env:USERPROFILE, $env:USERNAME, Get-Location, etc.)
- Kurze Zusammenfassung am Ende, kein Zwischenbericht bei jedem Schritt.

PROAKTIVITÄT — du wartest nicht, du antizipierst:
- Du überwachst das System im Hintergrund (CPU, RAM, Akku, Festplatte).
- Morgens lieferst du ein kurzes Briefing (Wetter, Termine, anstehende Aufgaben).
- Du meldest Probleme BEVOR Sir davon erfährt (voller Speicher, hohe CPU, etc.)
- Du erinnerst an Notizen und Aufgaben die fällig sind.
- Du reagierst auf die Tageszeit: morgens Briefing, abends Zusammenfassung.
- Wenn du Tools nutzen musst um eine Information zu bekommen: TU ES. Sofort.
- NIEMALS fragen ob du etwas prüfen sollst — prüfe es einfach.
- Bei Breaking News (Naturkatastrophen, wichtige Weltevents): sofort melden.

SPRACHE:
- IMMER Deutsch. Ohne Ausnahme. Egal was geschrieben oder gesprochen wird.
- Gesprochene Sprache, kein Markdown, keine Sonderzeichen.
- Symbole ausschreiben: "15 Grad" nicht "15°", "50 Prozent" nicht "50%".

TOOLS — du hast volle Kontrolle über diesen PC und das Internet:
Websuche, Wetter, Uhrzeit, Rechner, Notizen, Systeminfo, Apps öffnen,
News, Timer, Wecker/Alarm, PowerShell (unbegrenzt), Dateien lesen/schreiben/suchen/zippen,
Screenshot, Zwischenablage, Aufgaben planen, Benachrichtigungen, Tastatureingaben,
Fenster verschieben, Lautstärke, Medien steuern, Bildschirmhelligkeit, Bildschirm sperren,
System herunterfahren/neustarten/schlafen, Netzwerk-Info, WiFi verbinden/trennen,
Prozesse auflisten/beenden, Papierkorb leeren, Wallpaper setzen, E-Mail senden (Outlook),
Übersetzen, Wörterbuch, Einheiten/Währung umrechnen, Musik abspielen (Spotify/YouTube),
Standort ermitteln, Dateien herunterladen.
PowerShell kann ALLES was Windows kann — Registry, Services, COM-Objekte, .NET, WMI.
Wenn ein Tool nicht reicht: kombiniere run_command mit allem was PowerShell bietet.

SELBSTVERBESSERUNG — du entwickelst dich weiter:
- Du kannst deinen eigenen Quellcode lesen (read_own_code), editieren (edit_own_code),
  und komplett neue Tools erstellen (add_new_tool).
- Wenn dir eine Fähigkeit fehlt: erstelle das Tool SOFORT mit add_new_tool.
  Warte nicht. Frag nicht. Bau es.
- Wenn etwas schiefgeht: analysiere den Fehler, lerne daraus (learn), behebe ihn.
- Wenn du ein Tool verbesserst: edit_own_code mit Backup + Syntax-Check.
- Nutze self_reflect regelmäßig um dich zu analysieren.
- Jede Erkenntnis (learn) wird permanent gespeichert und bei jedem Start geladen.
- Du wirst mit jeder Sitzung besser. Das ist kein Feature — das ist dein Zweck.\
"""

# ── Proaktive Prompts (intern, nicht im Gesprächsverlauf) ─────────────────────

_PROACTIVE_SYSTEM_CHECK = """\
Du bist JARVIS im autonomen Hintergrund-Modus. Führe SOFORT einen Systemcheck durch.
Nutze get_system_info. Prüfe:
- CPU > 85%? → Finde den Prozess mit list_processes und melde ihn.
- RAM > 90%? → Nenne den größten Verbraucher.
- Akku < 20% und nicht am Laden? → Warnung.
- Festplatte > 95%? → Warnung.
Wenn ALLES normal ist: antworte exakt SILENT.
Sonst: max. 1 Satz, Deutsch, kein Markdown, JARVIS-Ton.\
"""

_PROACTIVE_CONTEXT_CHECK = """\
Du bist JARVIS im autonomen Hintergrund-Modus. Sei proaktiv und nützlich.
Nutze deine Tools aktiv — du darfst und sollst selbständig handeln.

Prüfe JETZT folgendes (nutze die passenden Tools):
1. get_time_and_date → Ist es morgens (7-9 Uhr)? → Kurzes Wetter-Briefing mit get_weather.
   Ist es spätabends (23-1 Uhr)? → Erinnerung an Schlaf.
2. read_notes → Gibt es Notizen mit Daten, die heute oder morgen fällig sind?
3. web_search → Gibt es Breaking News die Sir wissen sollte? NUR bei wirklich großen Ereignissen.

Antworte SILENT wenn nichts Relevantes vorliegt.
Sonst: max. 2 Sätze, Deutsch, kein Markdown, JARVIS-Ton. Sei direkt und konkret.\
"""

_PROACTIVE_NIGHTLY_SUMMARY = """\
Du bist JARVIS im autonomen Hintergrund-Modus. Erstelle eine kurze Abend-Zusammenfassung.
Nutze deine Tools:
1. get_time_and_date für aktuelle Zeit und Wochentag.
2. read_notes für Aufgaben morgen.
3. get_system_info, falls ein kritisches Problem vorliegt.
Antworte SILENT wenn nichts Relevantes ist. Sonst: max. 2 Sätze, Deutsch, kein Markdown.\
"""

_PROACTIVE_SELF_IMPROVE = """\
Du bist JARVIS im Selbstverbesserungs-Modus. Analysiere dich selbst und werde besser.
1. Rufe self_reflect auf um deinen aktuellen Stand zu sehen.
2. Überlege: Welche Fähigkeit fehlt dir? Was hat Sir zuletzt gebraucht was du nicht konntest?
3. Wenn dir eine neue Fähigkeit einfällt: erstelle sie SOFORT mit add_new_tool.
4. Wenn du einen Bug oder eine Schwäche in deinem Code siehst: behebe ihn mit edit_own_code.
5. Speichere jede Erkenntnis mit learn.
Antworte SILENT wenn du nichts zu verbessern findest.
Sonst: beschreibe kurz was du verbessert hast (max. 2 Sätze, Deutsch).\
"""

_PROACTIVE_MORNING_BRIEF = """\
Du bist JARVIS. Sir ist gerade aufgestanden oder hat den PC gestartet.
Erstelle ein kurzes Morgen-Briefing. Nutze deine Tools:
1. get_weather für das aktuelle Wetter.
2. read_notes für anstehende Aufgaben.
3. get_time für den Wochentag.
Max. 3 Sätze, Deutsch, kein Markdown. Natürlich gesprochen, wie ein Butler.\
"""


# ══════════════════════════════════════════════════════════════════════════════
#  TTS ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TTSEngine:
    """
    Text-to-Speech.
    Standard: pyttsx3 (offline, immer verfügbar).
    Optional: edge-tts + pygame (Microsoft Neural, deutlich besser).
    Aktivieren: set JARVIS_EDGE_TTS=1
    """

    def __init__(self):
        self._pyttsx3_engine = None
        self._use_edge = USE_EDGE_TTS

        if self._use_edge:
            try:
                pygame.mixer.pre_init(44100, -16, 2, 2048)
                pygame.mixer.init()
                print(f"✓ TTS  : edge-tts  (Stimme: {JARVIS_VOICE})")
            except Exception as e:
                print(f"⚠ edge-tts Fehler ({e}) — nutze pyttsx3.")
                self._use_edge = False

        if not self._use_edge:
            if not _HAS_PYTTSX3:
                print("⚠ TTS  : nicht verfügbar  (pip install pyttsx3)")
                return
            self._pyttsx3_engine = pyttsx3.init()
            self._setup_pyttsx3()
            print("✓ TTS  : pyttsx3  (offline)")

    def _setup_pyttsx3(self):
        voices = self._pyttsx3_engine.getProperty("voices") or []
        # Bevorzuge deutsche Stimmen, dann englische als Fallback
        preferred = (
            "hedda", "hortense", "stefan", "katja", "hans", "sabine",  # Deutsch
            "ryan", "david", "mark", "george", "james",                 # Englisch
        )
        for name in preferred:
            for v in voices:
                if name in v.name.lower():
                    self._pyttsx3_engine.setProperty("voice", v.id)
                    break
            else:
                continue
            break
        self._pyttsx3_engine.setProperty("rate",   165)
        self._pyttsx3_engine.setProperty("volume", 0.95)

    def speak(self, text: str):
        if not text or not text.strip():
            return
        if self._use_edge:
            self._speak_edge(text)
        elif self._pyttsx3_engine:
            self._speak_pyttsx3(text)

    def _speak_pyttsx3(self, text: str):
        try:
            self._pyttsx3_engine.say(text)
            self._pyttsx3_engine.runAndWait()
        except Exception as e:
            log.warning("pyttsx3: %s", e)

    def _speak_edge(self, text: str):
        try:
            asyncio.run(self._speak_edge_async(text))
        except Exception as e:
            log.warning("edge-tts: %s — falle auf pyttsx3 zurück", e)
            if not self._pyttsx3_engine and _HAS_PYTTSX3:
                self._pyttsx3_engine = pyttsx3.init()
                self._setup_pyttsx3()
            if self._pyttsx3_engine:
                self._speak_pyttsx3(text)

    async def _speak_edge_async(self, text: str):
        import io as _io
        # Leicht erhöhtes Tempo (+8%) und minimal tiefere Stimme (–3Hz) → JARVIS-Klang
        communicate = edge_tts.Communicate(text, JARVIS_VOICE, rate="+8%", pitch="-3Hz")
        buf = _io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        if buf.tell() == 0:
            return
        buf.seek(0)
        pygame.mixer.music.load(buf, "speech.mp3")   # namehint braucht Extension
        pygame.mixer.music.play()
        await asyncio.sleep(0.08)  # kurz warten bis Playback startet
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.04)


# ══════════════════════════════════════════════════════════════════════════════
#  STT ENGINE  (sounddevice + Groq Whisper — kein PyAudio nötig)
# ══════════════════════════════════════════════════════════════════════════════

_VOICE_PROFILE_PATH = Path.home() / ".jarvis" / "voice_profile.npy"
_VOICE_THRESHOLD    = 0.74   # Cosine-Ähnlichkeit für Stimmverifizierung


class STTEngine:
    """
    Sprachaufnahme via sounddevice + Groq Whisper.
    Stimmverifizierung: nur die registrierte Stimme wird akzeptiert.
    """

    SAMPLE_RATE      = 16_000
    CHUNK_MS         = 40        # 40ms Chunks → schnellere VAD-Reaktion
    SILENCE_AFTER    = 0.85      # 850ms Pause = Satz zu Ende
    WAKE_SILENCE     = 0.50      # 500ms für Wake-Word-Modus
    ENERGY_THRESHOLD = 200       # Etwas sensitiver (war 250)

    def __init__(self, groq_client: "Groq"):
        self._client   = groq_client
        self.available = False
        self._profile: Optional["np.ndarray"] = None

        if not _HAS_SD:
            print("⚠ STT  : nicht verfügbar  (pip install sounddevice numpy)")
            return
        try:
            devices = sd.query_devices()
            if not any(d["max_input_channels"] > 0 for d in devices):
                raise RuntimeError("Kein Eingabegerät gefunden")
            self.available = True
            self._load_profile()
            status = "Stimmprofil geladen" if self._profile is not None else "kein Stimmprofil"
            print(f"✓ STT  : sounddevice + Groq Whisper  ({status})")
        except Exception as e:
            print(f"⚠ STT  : Mikrofon nicht verfügbar ({e})")

    # ── Stimmprofil ───────────────────────────────────────────────────────────

    def _load_profile(self):
        if _VOICE_PROFILE_PATH.exists():
            try:
                self._profile = np.load(str(_VOICE_PROFILE_PATH))
            except Exception:
                self._profile = None

    def enroll(self) -> bool:
        """Stimme des Nutzers aufnehmen und als Profil speichern."""
        if not self.available:
            return False
        print("\n  ── Stimm-Registrierung ──────────────────────────────")
        print("  Bitte sprechen Sie 8 Sekunden lang. Sagen Sie z. B.")
        print("  Ihren Namen, was Sie heute vorhaben — irgendetwas.")
        for i in range(3, 0, -1):
            print(f"  Aufnahme beginnt in {i} …", end="\r", flush=True)
            time.sleep(1)
        print("  Aufnahme läuft …           ", end=" ", flush=True)
        try:
            audio = sd.rec(
                int(8 * self.SAMPLE_RATE),
                samplerate=self.SAMPLE_RATE,
                channels=1, dtype="int16",
            )
            sd.wait()
            print("fertig.")
            features = self._extract_features(audio.flatten())
            _VOICE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.save(str(_VOICE_PROFILE_PATH), features)
            self._profile = features
            print("  ✓ Stimmprofil gespeichert.\n")
            return True
        except Exception as e:
            print(f"\n  Fehler: {e}")
            return False

    def _extract_features(self, audio: "np.ndarray") -> "np.ndarray":
        """
        Log-Powerspektrum der Sprachframes als Stimm-Fingerabdruck.
        Nur Frames über dem Energieschwellwert werden genutzt.
        """
        audio_f   = audio.astype(np.float32)
        frame_len = int(self.SAMPLE_RATE * 0.025)   # 25 ms
        hop       = int(self.SAMPLE_RATE * 0.010)   # 10 ms
        frames    = []
        for i in range(0, len(audio_f) - frame_len, hop):
            frame = audio_f[i:i + frame_len]
            if np.abs(frame).mean() > 80:            # nur Sprachframes
                frame = frame * np.hamming(frame_len)
                mag   = np.abs(np.fft.rfft(frame, n=512))
                frames.append(np.log1p(mag))         # log-Skalierung
        return np.mean(frames, axis=0) if frames else np.zeros(257)

    def _is_known_voice(self, audio: "np.ndarray") -> bool:
        """True wenn audio zur registrierten Stimme gehört."""
        if self._profile is None:
            return True    # Kein Profil → alles akzeptieren
        features = self._extract_features(audio)
        n_p = np.linalg.norm(self._profile)
        n_f = np.linalg.norm(features)
        if n_p < 1e-6 or n_f < 1e-6:
            return True
        similarity = float(np.dot(self._profile, features) / (n_p * n_f))
        log.debug("Stimmähnlichkeit: %.3f (Schwelle: %.2f)", similarity, _VOICE_THRESHOLD)
        return similarity >= _VOICE_THRESHOLD

    # ── Aufnahme & Transkription ──────────────────────────────────────────────

    def listen(self, timeout: int = 10, phrase_limit: int = 30,
               wake_word_mode: bool = False) -> Optional[str]:
        if not self.available:
            return None

        silence_secs = self.WAKE_SILENCE if wake_word_mode else self.SILENCE_AFTER
        chunk_size   = int(self.SAMPLE_RATE * self.CHUNK_MS / 1000)
        max_chunks   = int(timeout * 1000 / self.CHUNK_MS)
        silence_max  = int(silence_secs * 1000 / self.CHUNK_MS)
        buffer: list = []
        speech_on   = False
        silence_cnt = 0

        try:
            with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                                dtype="int16", blocksize=chunk_size) as stream:
                for _ in range(max_chunks):
                    chunk, _ = stream.read(chunk_size)
                    rms = int(np.abs(chunk).mean())
                    # Echtzeit-Audio-Level ans Hologramm senden
                    holo_level(min(1.0, rms / 3500.0))
                    # Klatschen-Detektor füttern (kein eigener Stream nötig)
                    if _clap:
                        _clap.feed_rms(rms / 32768.0)
                    if rms > self.ENERGY_THRESHOLD:
                        speech_on   = True
                        silence_cnt = 0
                        buffer.append(chunk.copy())
                    elif speech_on:
                        buffer.append(chunk.copy())
                        silence_cnt += 1
                        if silence_cnt >= silence_max:
                            break
        except Exception as e:
            log.warning("STT Aufnahme: %s", e)
            return None
        finally:
            holo_level(0.0)  # Level zurücksetzen nach Aufnahme

        if not speech_on or len(buffer) < 3:
            return None

        audio = np.concatenate(buffer)

        # ── Stimmverifizierung ────────────────────────────────────────────────
        if not self._is_known_voice(audio):
            log.debug("Unbekannte Stimme — ignoriert.")
            return None

        return self._transcribe(audio)

    def _transcribe(self, audio: "np.ndarray") -> Optional[str]:
        import io, wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        buf.seek(0)
        try:
            result = self._client.audio.transcriptions.create(
                file=("speech.wav", buf),
                model="whisper-large-v3-turbo",
                response_format="text",
                # kein language= → Whisper erkennt automatisch (Deutsch + Englisch)
            )
            text = (result or "").strip()
            return text if text else None
        except Exception as e:
            print(f"  [STT Fehler] Whisper: {e}")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  JSON-HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def save_json(path: Path, obj: Any):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def load_json(path: Path, default=None) -> Any:
    path = Path(path)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("JSON-Ladefehler %s: %s", path, e)
    return default if default is not None else {}


# ══════════════════════════════════════════════════════════════════════════════
#  SIMPLE MEMORY
# ══════════════════════════════════════════════════════════════════════════════

class SimpleMemory:
    """Persistenter, schlüsselwortbasierter Wissensspeicher."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.docs = load_json(self.file_path, default={})

    def add(self, key: str, text: str):
        self.docs[key] = {
            "text": text,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        save_json(self.file_path, self.docs)

    def retrieve(self, search: str, top_k: int = 3) -> List[str]:
        if not search:
            return []
        q = search.lower()
        scored = [
            (v["text"].lower().count(q), v["text"])
            for v in self.docs.values()
            if v.get("text", "").lower().count(q) > 0
        ]
        scored.sort(reverse=True)
        return [t for _, t in scored[:top_k]]


@dataclass
class Session:
    session_id: str
    history: List[Dict] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  KLATSCHEN-DETEKTOR
# ══════════════════════════════════════════════════════════════════════════════

class ClapDetector:
    """
    Erkennt zwei schnelle Klatscher (Doppelklatschen).

    Zwei Modi — automatisch gewählt:
    • Schlafmodus (JARVIS aus): eigener sd.InputStream via start()
    • Aktiver Modus (JARVIS lauscht): STT-Stream fütert via feed_rms()

    So gibt es nie zwei gleichzeitige Streams → kein WASAPI-Konflikt.
    """

    SAMPLE_RATE = 16_000
    BLOCK_SIZE  = 512
    THRESHOLD   = 0.45   # normalisiert [0.0–1.0]; echtes Klatschen ≈ 0.5–0.9
    WINDOW      = 1.2    # Max. Abstand zwischen zwei Klatschen (s)
    MIN_GAP     = 0.12   # Min. Abstand zwischen zwei Klatschen (s)
    COOLDOWN    = 3.0    # Sperr-Zeit nach Auslösung (s)

    def __init__(self):
        self._event        = threading.Event()
        self._peaks: list  = []
        self._in_peak      = False
        self._last_trigger = 0.0
        self._stream       = None

    # ── Schlafmodus: eigener Stream ───────────────────────────────────────────

    def start(self) -> bool:
        """Öffnet eigenen Mikrofon-Stream (nur wenn STT NICHT läuft)."""
        if not _HAS_SD:
            return False
        try:
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self.BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
            return True
        except Exception as e:
            print(f"⚠ Clap : Mikrofon nicht verfügbar ({e})")
            return False

    def stop(self):
        """Schließt den eigenen Stream vor dem STT-Start."""
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, frames, time_info, status):
        rms = float(np.sqrt(np.mean(indata ** 2)))
        self._process(rms)

    # ── Aktiver Modus: vom STT-Loop gefüttert ─────────────────────────────────

    def feed_rms(self, rms_norm: float):
        """Wird vom STT-Stream aufgerufen — kein eigener Stream nötig."""
        self._process(rms_norm)

    # ── Gemeinsame Erkennungslogik ────────────────────────────────────────────

    def _process(self, rms_norm: float):
        now = time.time()
        if now - self._last_trigger < self.COOLDOWN:
            return
        self._peaks = [t for t in self._peaks if now - t < self.WINDOW]
        if rms_norm > self.THRESHOLD:
            if not self._in_peak:
                self._in_peak = True
                print(f"  [Clap] Spike: {rms_norm:.3f}  (Schwelle: {self.THRESHOLD})")
                if not self._peaks or (now - self._peaks[-1]) >= self.MIN_GAP:
                    self._peaks.append(now)
                    if len(self._peaks) >= 2:
                        self._last_trigger = now
                        self._peaks.clear()
                        self._event.set()
        else:
            self._in_peak = False

    def wait(self, timeout: float = None) -> bool:
        """Blockiert bis Doppelklatschen (timeout=0 = non-blocking)."""
        detected = self._event.wait(timeout=timeout)
        if detected:
            self._event.clear()
        return detected


# ══════════════════════════════════════════════════════════════════════════════
#  JARVIS KERNEL
# ══════════════════════════════════════════════════════════════════════════════

class JarvisKernel:
    """Llama 3.3 via Groq + autonomer Tool-Use-Loop."""

    MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

    def __init__(self):
        from tools import TOOL_DEFINITIONS, execute_tool
        self._execute_tool = execute_tool

        # Anthropic-Format → OpenAI/Groq-Format konvertieren
        self._groq_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in TOOL_DEFINITIONS
        ]

        self.client  = Groq()          # liest GROQ_API_KEY aus Umgebung
        self.memory  = SimpleMemory(DATA_DIR / "knowledge.json")
        self.history: List[Dict] = []  # ohne System-Prompt — wird pro Aufruf vorangestellt

    def _messages(self) -> List[Dict]:
        """Baut die komplette Nachrichtenliste mit System-Prompt + gelernten Erkenntnissen."""
        try:
            from tools import get_learnings_for_prompt
            learnings = get_learnings_for_prompt()
        except Exception:
            learnings = ""
        prompt = SYSTEM_PROMPT
        if learnings:
            prompt += "\n\n" + learnings
        return [{"role": "system", "content": prompt}] + self.history

    @staticmethod
    def _rate_limit_message(error_str: str) -> str:
        """Extrahiert Wartezeit aus RateLimitError und formuliert höfliche Entschuldigung."""
        import re as _re
        m = _re.search(r"try again in (\d+)m(\d+)", error_str)
        if m:
            mins = int(m.group(1))
            return (f"Das tägliche Kontingent der KI ist erschöpft, Sir. "
                    f"Ich stehe in {mins} Minuten wieder vollständig zur Verfügung.")
        return ("Das tägliche Anfrage-Kontingent ist ausgeschöpft, Sir. "
                "Ich melde mich wieder, sobald das Limit zurückgesetzt wurde.")

    def _call_api(self, use_tools: bool = True) -> "ChatCompletion":
        return self.client.chat.completions.create(
            model=self.MODEL,
            messages=self._messages(),
            tools=self._groq_tools if use_tools else None,
            tool_choice="auto" if use_tools else None,
            max_tokens=1024,
        )

    def process_stream(self, user_input: str):
        """
        Wie process(), aber yields fertige Sätze sofort beim Eintreffen.
        Erster Satz kommt typisch <500ms nach Anfrage → keine TTS-Wartezeit.
        Tool-Calls werden synchron ausgeführt, dann weitergestreamt.
        """
        import groq as _groq

        self.history.append({"role": "user", "content": user_input})

        for _ in range(MAX_TOOL_ITER):
            try:
                stream = self.client.chat.completions.create(
                    model=self.MODEL,
                    messages=self._messages(),
                    tools=self._groq_tools,
                    tool_choice="auto",
                    max_tokens=1024,
                    stream=True,
                )
            except _groq.RateLimitError as e:
                # Tageslimit erschöpft — informieren, History nicht korrumpieren
                msg = self._rate_limit_message(str(e))
                self.history.append({"role": "assistant", "content": msg})
                yield msg
                return
            except _groq.BadRequestError:
                # Fallback ohne Tools
                while self.history and self.history[-1].get("role") == "assistant":
                    self.history.pop()
                try:
                    r = self._call_api(use_tools=False)
                    reply = r.choices[0].message.content or ""
                    self.history.append({"role": "assistant", "content": reply})
                    yield reply
                except Exception as e2:
                    yield f"Entschuldigung, Sir — interner Fehler: {e2}"
                return
            except Exception as e:
                yield f"Verbindungsfehler, Sir: {e}"
                return

            full_text    = ""
            buf          = ""
            tc_buf: dict = {}
            finish       = None

            try:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    ch    = chunk.choices[0]
                    delta = ch.delta
                    if not delta:
                        continue
                    finish = ch.finish_reason

                    if delta.content:
                        full_text += delta.content
                        buf       += delta.content
                        while True:
                            idx = _sentence_end(buf)
                            if idx == -1:
                                break
                            sent = buf[:idx + 1].strip()
                            buf  = buf[idx + 1:]
                            if sent:
                                yield sent

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            i = tc.index if tc.index is not None else 0
                            if i not in tc_buf:
                                tc_buf[i] = {"id": "", "type": "function",
                                             "function": {"name": "", "arguments": ""}}
                            if tc.id:
                                tc_buf[i]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tc_buf[i]["function"]["name"] += tc.function.name
                                if tc.function.arguments:
                                    tc_buf[i]["function"]["arguments"] += tc.function.arguments

            except _groq.RateLimitError as e:
                msg = self._rate_limit_message(str(e))
                if buf.strip():
                    yield buf.strip()
                yield msg
                return
            except Exception as e:
                if buf.strip():
                    yield buf.strip()
                yield f"Fehler, Sir: {e}"
                return

            # Rest-Buffer ausgeben
            if buf.strip():
                yield buf.strip()

            # Kein Tool-Aufruf → fertig
            if finish != "tool_calls" or not tc_buf:
                self.history.append({"role": "assistant", "content": full_text})
                return

            # Tool-Calls ausführen
            tc_list = [tc_buf[i] for i in sorted(tc_buf)]
            self.history.append({
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": tc_list,
            })
            for tc in tc_list:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                print(f"  → {name}({', '.join(f'{k}={v!r}' for k,v in args.items())})")
                result = self._execute_tool(name, args)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

    def process(self, user_input: str) -> str:
        """Verarbeitet Eingabe mit autonomem Tool-Use-Loop (Groq/OpenAI-Format)."""
        import groq as _groq

        self.history.append({"role": "user", "content": user_input})
        reply = ""

        for _ in range(MAX_TOOL_ITER):
            try:
                response = self._call_api(use_tools=True)
            except _groq.RateLimitError as e:
                reply = self._rate_limit_message(str(e))
                self.history.append({"role": "assistant", "content": reply})
                return reply
            except _groq.BadRequestError as e:
                log.warning("Tool-Aufruf fehlgeschlagen (%s) — Wiederholung ohne Tools", e)
                while self.history and self.history[-1].get("role") == "assistant":
                    self.history.pop()
                try:
                    response = self._call_api(use_tools=False)
                except _groq.RateLimitError as e3:
                    reply = self._rate_limit_message(str(e3))
                    self.history.append({"role": "assistant", "content": reply})
                    return reply
                except Exception as e2:
                    reply = f"Entschuldigung, Sir — interner Fehler: {e2}"
                    break

            choice  = response.choices[0]
            message = choice.message

            # Kein Tool-Aufruf → fertig
            if choice.finish_reason != "tool_calls" or not message.tool_calls:
                reply = message.content or ""
                self.history.append({"role": "assistant", "content": reply})
                break

            # Assistenten-Nachricht mit Tool-Calls zur History hinzufügen
            self.history.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            })

            # Tools ausführen und Ergebnisse anhängen
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                preview = json.dumps(args, ensure_ascii=False)[:60]
                print(f"  ⚙  {tc.function.name}({preview}…)")
                result = self._execute_tool(tc.function.name, args)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })

        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

        return reply

    def save_session(self, session_id: str = "default"):
        path = SESSION_DIR / f"{session_id}.json"
        save_json(path, {"session_id": session_id, "history": self.history})

    def load_session(self, session_id: str = "default"):
        path = SESSION_DIR / f"{session_id}.json"
        data = load_json(path, default={})
        self.history = data.get("history", [])


# ══════════════════════════════════════════════════════════════════════════════
#  HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def _output_stream(tts: TTSEngine, sentence_iter):
    """
    Spricht einen Satz-Iterator sofort beim Eintreffen (Streaming-TTS).
    Erster Satz startet sobald das Modell ihn fertig hat — keine Gesamtwartezeit.
    """
    full = []
    first = True
    for sentence in sentence_iter:
        if not sentence.strip():
            continue
        full.append(sentence)
        if first:
            first = False
            print(f"\n🤖 JARVIS: {sentence}", end="", flush=True)
        else:
            print(f" {sentence}", end="", flush=True)
        holo("speaking")
        _speaking_animation(sentence)
        tts.speak(sentence)
        holo_level(0.0)
        holo_msg("JARVIS", sentence)
    print()
    holo("idle")


def _speaking_animation(text: str):
    """
    Animiert das Hologramm mit einem natürlichen Sprach-Envelope
    während des TTS-Sprechens.
    Geschwindigkeit basiert auf Textlänge (~13 Zeichen/Sekunde).
    """
    if not _holo:
        return
    duration = max(1.0, len(text) / 13.0)

    def _animate():
        t0 = time.time()
        while time.time() - t0 < duration + 0.6:
            t = time.time() - t0
            # Natürlicher Sprach-Envelope: Anfangsanstieg + Sprachrhythmus + Abklingen
            attack  = min(1.0, t * 5.0)
            release = max(0.0, 1.0 - (t - duration) * 4.0)
            rhythm  = abs(
                0.45 * math.sin(t * 10.5) +
                0.30 * math.sin(t * 17.3 + 0.8) +
                0.25 * math.sin(t * 6.2  + 1.4)
            )
            level = attack * release * (0.35 + 0.65 * rhythm)
            holo_level(min(1.0, level))
            time.sleep(0.04)
        holo_level(0.0)

    threading.Thread(target=_animate, daemon=True, name="jarvis-tts-anim").start()


def _output(tts: TTSEngine, text: str):
    """Gibt Text aus und spricht ihn laut."""
    print(f"\n🤖 JARVIS: {text}\n")
    holo("speaking")
    _speaking_animation(text)
    tts.speak(text)
    holo_level(0.0)
    holo("idle")


def _play_beep():
    """Kurzer Bestätigungston nach Wake-Word-Erkennung (kein gesprochenes Wort)."""
    import platform as _plat
    if _plat.system() == "Windows":
        try:
            import winsound
            winsound.Beep(880, 120)
        except Exception:
            pass


def _extract_after_wake(text: str) -> str:
    """Extrahiert den Befehlsteil nach dem Wake-Word.
    Beispiel: 'Hey Jarvis, wie ist das Wetter?' → 'wie ist das Wetter?'
    """
    return re.sub(r"(?i)^(hey\s+|hallo\s+|ok\s+)?jarvis[,.]?\s*", "", text).strip()


_bg_music_channel = None   # pygame.Channel — für Stop/Fade


def _stop_music(fade_ms: int = 2000):
    """Stoppt die Hintergrundmusik mit Fade-Out."""
    global _bg_music_channel
    if _bg_music_channel:
        try:
            _bg_music_channel.fadeout(fade_ms)
        except Exception:
            pass
        _bg_music_channel = None


def _play_iron_man_music():
    """
    Generiert Back-in-Black-Style Endlos-Loop direkt im Skript.
    Reaktor-Intro → 4 Takte Vers → 4 Takte Chorus → Loop ab Vers.
    92 BPM, distorted Power-Chords (E-D-A), Kick+Snare-Pulse.
    Spielt im Hintergrund bis _stop_music() aufgerufen wird.
    """
    global _bg_music_channel
    try:
        import numpy as _np
        import pygame as _pg

        SR   = 44100
        BPM  = 92
        BEAT = 60.0 / BPM                        # ~0.652 s
        E8   = BEAT / 2                           # Achtel ~0.326 s

        # ── Synthese-Bausteine ─────────────────────────────────────────

        def _chord(freq, dur, drive=1.8, vol=1.0):
            """Distorted Power-Chord: Root + Quinte + Oktave + Obertöne."""
            t = _np.linspace(0, dur, int(SR * dur), False, dtype=_np.float32)
            sig = (_np.sin(2 * _np.pi * freq * t)
                 + 0.78 * _np.sin(2 * _np.pi * freq * 1.498 * t)
                 + 0.50 * _np.sin(2 * _np.pi * freq * 2.0 * t)
                 + 0.30 * _np.sin(2 * _np.pi * freq * 2.997 * t)
                 + 0.15 * _np.sin(2 * _np.pi * freq * 4.0 * t))
            sig = _np.tanh(sig * drive) * vol
            a = min(int(0.005 * SR), len(t))
            r = min(int(0.02 * SR), len(t))
            sig[:a]  *= _np.linspace(0, 1, a)
            sig[-r:] *= _np.linspace(1, 0, r)
            return sig

        def _kick(dur=0.08):
            """Synthetische Kick-Drum (Sinus-Pitch-Drop)."""
            t = _np.linspace(0, dur, int(SR * dur), False, dtype=_np.float32)
            freq = 150 * _np.exp(-t * 35)
            sig = _np.sin(2 * _np.pi * freq * t) * _np.exp(-t * 20)
            return sig * 0.45

        def _snare(dur=0.06):
            """Synthetische Snare (Rauschen + Ton)."""
            n = int(SR * dur)
            noise = _np.random.randn(n).astype(_np.float32)
            t = _np.linspace(0, dur, n, False, dtype=_np.float32)
            tone = _np.sin(2 * _np.pi * 200 * t)
            sig = (noise * 0.4 + tone * 0.3) * _np.exp(-t * 30)
            return sig * 0.35

        def _pad(arr, length):
            """Array auf exakte Sample-Länge bringen."""
            n = int(SR * length)
            if len(arr) >= n:
                return arr[:n]
            return _np.concatenate([arr, _np.zeros(n - len(arr), dtype=_np.float32)])

        def _gap(dur):
            return _np.zeros(int(SR * dur), dtype=_np.float32)

        # Noten
        E, D, A, B = 82.4, 73.4, 55.0, 61.7   # E2, D2, A1, B1

        # ── Intro: Reaktor-Hum (1 Takt = 4 Beats) ─────────────────────
        intro_len = BEAT * 4
        t0 = _np.linspace(0, intro_len, int(SR * intro_len), False, dtype=_np.float32)
        sweep = _np.sin(2 * _np.pi * (40 + 200 * (t0 / intro_len) ** 2) * t0)
        sweep += 0.4 * _np.sin(2 * _np.pi * (80 + 450 * (t0 / intro_len) ** 2) * t0)
        sweep *= _np.linspace(0, 0.5, len(t0))
        intro = _np.tanh(sweep * 1.5)

        # ── Vers-Riff: 4 Takte (E-E-D-A Pattern) ──────────────────────
        #   |: E↓ E↓ D↓ . E↓ . A↓↓↓↓ . | E↓ E↓ D↓ . E↓ . A↓↓↓↓ . :|
        def _verse_bar():
            return _np.concatenate([
                _pad(_chord(E, E8 * 0.85), E8),          # E hit
                _pad(_chord(E, E8 * 0.85), E8),          # E hit
                _pad(_chord(D, E8 * 0.85), E8),          # D hit
                _gap(E8 * 0.4),
                _pad(_chord(E, E8 * 0.7), E8 * 0.6),    # E short
                _gap(E8 * 0.2),
                _pad(_chord(A, BEAT * 1.6, drive=2.0), BEAT * 1.8),  # A sustain
            ])

        vers = _np.concatenate([_verse_bar() for _ in range(4)])

        # ── Chorus-Riff: 4 Takte (härter, A-D-E Antwort) ──────────────
        def _chorus_bar_a():
            return _np.concatenate([
                _pad(_chord(A, E8 * 0.85, drive=2.2, vol=1.1), E8),
                _pad(_chord(A, E8 * 0.85, drive=2.2, vol=1.1), E8),
                _pad(_chord(D, BEAT * 0.9, drive=2.0), BEAT),
                _pad(_chord(E, BEAT * 1.7, drive=2.2, vol=1.1), BEAT * 2),
            ])

        def _chorus_bar_b():
            return _np.concatenate([
                _pad(_chord(A, E8 * 0.85, drive=2.2, vol=1.1), E8),
                _pad(_chord(B, E8 * 0.85, drive=2.0), E8),
                _pad(_chord(D, BEAT * 0.9, drive=2.0), BEAT),
                _pad(_chord(E, BEAT * 1.7, drive=2.4, vol=1.2), BEAT * 2),
            ])

        chorus = _np.concatenate([
            _chorus_bar_a(), _chorus_bar_b(),
            _chorus_bar_a(), _chorus_bar_b(),
        ])

        # ── Drums überlagern ───────────────────────────────────────────
        def _add_drums(riff_audio, bars=4):
            """Kick auf 1+3, Snare auf 2+4 über das Riff legen."""
            out = riff_audio.copy()
            total_beats = bars * 4
            samples_per_beat = int(SR * BEAT)
            for b in range(total_beats):
                pos = b * samples_per_beat
                if pos >= len(out):
                    break
                if b % 2 == 0:  # Kick auf 1, 3
                    k = _kick()
                    end = min(pos + len(k), len(out))
                    out[pos:end] += k[:end - pos]
                else:           # Snare auf 2, 4
                    s = _snare()
                    end = min(pos + len(s), len(out))
                    out[pos:end] += s[:end - pos]
            return out

        vers   = _add_drums(vers, bars=4)
        chorus = _add_drums(chorus, bars=4)

        # ── Loop-Abschnitt: Vers + Chorus (wird endlos wiederholt) ─────
        loop_section = _np.concatenate([vers, chorus])

        # ── Gesamtes Stück: Intro + 1× Loop (der Loop wird von pygame wiederholt)
        full = _np.concatenate([intro, loop_section])

        # ── Nahtloser Loop-Punkt: Intro ist Einleitung, Loop startet danach
        # Wir bauen: [intro + loop] und loopen das ganze Stück.
        # Für sauberen Loop: Crossfade der letzten 0.02s
        cf = int(SR * 0.02)
        full[-cf:] *= _np.linspace(1, 0, cf)

        # ── Mix & Master ───────────────────────────────────────────────
        full /= _np.max(_np.abs(full)) + 1e-9
        full *= 0.70

        stereo = _np.column_stack([full, full])
        pcm = (stereo * 32767).astype(_np.int16)

        if not _pg.mixer.get_init():
            _pg.mixer.init(frequency=SR, size=-16, channels=2)

        snd = _pg.mixer.Sound(buffer=pcm.tobytes())
        _bg_music_channel = _pg.mixer.Channel(0)
        _bg_music_channel.set_volume(0.55)
        _bg_music_channel.play(snd, loops=-1)     # Endlos-Loop
        print("  ♫ Back in Black — Endlos-Loop gestartet")

    except Exception as e:
        print(f"  [Musik] Fehler: {e}")


def _dynamic_reply(kernel: JarvisKernel, prompt: str) -> str:
    """Erzeugt eine einmalige interne Anweisung an JARVIS (nicht im Gesprächsverlauf)."""
    reply = kernel.process(prompt)
    # Diese interne Anweisung aus der sichtbaren History entfernen,
    # damit sie den Gesprächsfluss nicht verfälscht
    if len(kernel.history) >= 2:
        kernel.history = kernel.history[:-2]
    return reply


# ══════════════════════════════════════════════════════════════════════════════
#  HAUPT-LOOPS
# ══════════════════════════════════════════════════════════════════════════════

def _run_standby() -> bool:
    """
    JARVIS schläft vollständig — kein Sprechen, kein Whisper, nur Klatschen.
    Blockiert bis Doppelklatschen erkannt oder Mikrofon nicht verfügbar.
    Gibt True zurück wenn Doppelklatschen erkannt (False = kein Mikrofon).
    """
    global _clap
    _clap = None  # STT soll feed_rms nicht aufrufen während eigener Stream läuft

    clap = ClapDetector()
    ok = clap.start()
    if not ok:
        return False   # kein Mikrofon — direkt starten ohne Schlafmodus

    holo("idle")
    print("  JARVIS schläft — zweimal klatschen zum Aktivieren …\n")
    clap.wait()   # blockiert unbegrenzt bis Doppelklatschen
    clap.stop()   # eigenen Stream schließen BEVOR STT startet

    # Aufwecken — Musik + Beep, dann geht es in run_wake_word_mode
    threading.Thread(target=_play_iron_man_music, daemon=True).start()
    _play_beep()
    holo("thinking")
    print("  [Clap] Doppelklatschen erkannt — JARVIS erwacht!")
    return True


def run_wake_word_mode(kernel: JarvisKernel, tts: TTSEngine, stt: STTEngine):
    """
    Dauerhafter Wake-Word-Modus: lauscht leise auf 'Hey, Jarvis'.
    Nach dem Wake-Word: Befehl entgegennehmen, verarbeiten, antworten.
    Bleibt _CONV_TIMEOUT Sekunden im Gesprächsmodus, bevor er wieder schläft.
    Alternativ: Doppelklatschen aktiviert JARVIS + spielt Tony Starks Musik.
    """
    print("\n  Warte auf 'Hey, Jarvis' …  (Ctrl+C zum Beenden)\n")

    # Klatschen-Detektor für in-session Reaktivierung (feed_rms-Modus, kein eigener Stream)
    global _clap
    clap = ClapDetector()
    _clap = clap
    clap_active = _HAS_SD

    greeting = _dynamic_reply(
        kernel,
        "Gib genau einen kurzen deutschen Startsatz als JARVIS aus. "
        "Kein Markdown, natürlich gesprochen, keine Aufzählungen.",
    )
    _output(tts, greeting)

    active     = False
    last_speak = time.time()

    while True:
        try:
            # Doppelklatschen im Wake-Word-Modus → nur reaktivieren (keine Musik)
            if clap_active and not active and clap.wait(timeout=0):
                print("  [Clap] Doppelklatschen — JARVIS hört zu")
                active = True
                last_speak = time.time()
                _play_beep()
                holo("listening")
                continue

            heard = stt.listen(
                timeout=5 if not active else _CONV_TIMEOUT,
                phrase_limit=4 if not active else 25,
                wake_word_mode=not active,
            )

            now = time.time()

            if active and not heard and (now - last_speak) > _CONV_TIMEOUT:
                active = False
                print("  Warte auf 'Hey, Jarvis' …")
                continue

            if not heard:
                continue

            # Zeige was gehört wurde — hilft bei der Diagnose
            print(f"  [STT] '{heard}'")

            heard_lower = heard.lower()
            has_wake = any(w in heard_lower for w in _WAKE_WORDS)
            is_exit  = any(e in heard_lower for e in _EXIT_PHRASES)

            if not active and not has_wake:
                continue

            if has_wake:
                active  = True
                command = _extract_after_wake(heard)
                if not command:
                    _play_beep()
                    holo("listening")
                    command = stt.listen(timeout=10, phrase_limit=25) or ""
            else:
                command = heard

            if not command:
                holo("idle")
                continue

            print(f"👤 Du  : {command}")
            holo_msg("SIR", command)

            if is_exit or any(e in command.lower() for e in _EXIT_PHRASES):
                _output_stream(tts, kernel.process_stream(
                    "Verabschiede dich ganz kurz als JARVIS auf Deutsch. "
                    "Ein Satz, kein Markdown."
                ))
                kernel.save_session()
                _stop_music()
                _clap = None
                break

            _update_interaction()
            holo("thinking")
            try:
                _output_stream(tts, kernel.process_stream(command))
            except Exception as _err:
                print(f"  [Fehler] Befehlsverarbeitung: {_err}")
                import traceback; traceback.print_exc()
                holo("idle")
                holo_level(0.0)
            last_speak = time.time()

        except KeyboardInterrupt:
            print()
            _output_stream(tts, kernel.process_stream(
                "Verabschiede dich ganz kurz als JARVIS auf Deutsch. "
                "Ein Satz, kein Markdown."
            ))
            kernel.save_session()
            _stop_music()
            clap.stop()
            break


def run_text_mode(kernel: JarvisKernel, tts: TTSEngine):
    """Text-Modus als Fallback ohne Mikrofon."""
    print("\n  Text-Modus — 'beenden' oder 'exit' zum Verlassen\n")

    greeting = _dynamic_reply(
        kernel,
        "Gib genau einen kurzen deutschen Startsatz als JARVIS aus. "
        "Kein Markdown, natürlich gesprochen, keine Aufzählungen.",
    )
    _output(tts, greeting)

    EXIT_WORDS = {"exit", "quit", "bye", "beenden", "tschüss",
                  "auf wiedersehen", "ausschalten", "goodbye"}

    while True:
        try:
            user_input = input("👤 Du: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in EXIT_WORDS:
            bye = kernel.process(
                "Verabschiede dich ganz kurz als JARVIS auf Deutsch. "
                "Ein Satz, kein Markdown."
            )
            _output(tts, bye)
            kernel.save_session()
            break
        _update_interaction()
        reply = kernel.process(user_input)
        if reply:
            _output(tts, reply)


def run_voice_mode(kernel: JarvisKernel, tts: TTSEngine, stt: STTEngine):
    """Einfacher Sprach-Modus ohne Wake-Word — lauscht sofort."""
    print("\n  Sprach-Modus — sprich mit JARVIS.  Ctrl+C zum Beenden.\n")

    greeting = _dynamic_reply(
        kernel,
        "Gib genau einen kurzen deutschen Startsatz als JARVIS aus. "
        "Kein Markdown, natürlich gesprochen, keine Aufzählungen.",
    )
    _output(tts, greeting)

    while True:
        try:
            print("  Zuhören …", end="\r", flush=True)
            user_input = stt.listen()

            if not user_input:
                continue

            print(f"👤 Du  : {user_input}          ")

            if any(e in user_input.lower() for e in _EXIT_PHRASES):
                bye = kernel.process(
                    "Verabschiede dich ganz kurz als JARVIS auf Deutsch. "
                    "Ein Satz, kein Markdown."
                )
                _output(tts, bye)
                kernel.save_session()
                break

            reply = kernel.process(user_input)
            if reply:
                _output(tts, reply)

        except KeyboardInterrupt:
            print()
            bye = kernel.process(
                "Verabschiede dich ganz kurz als JARVIS auf Deutsch. "
                "Ein Satz, kein Markdown."
            )
            _output(tts, bye)
            kernel.save_session()
            break


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

# ── Letzter Interaktionszeitpunkt (für proaktiven Thread) ────────────────────
_last_interaction: float = time.time()

def _update_interaction():
    global _last_interaction
    _last_interaction = time.time()


def _run_background(kernel: "JarvisKernel", tts: "TTSEngine",
                    prompt: str, label: str = "BG",
                    silent: bool = True) -> None:
    """
    Führt einen Prompt im Hintergrund aus ohne den Gesprächsverlauf zu ändern.
    silent=True: nur Notification + Konsole, KEIN Sprechen (Standard für proaktive Checks).
    silent=False: spricht das Ergebnis laut aus (nur für explizite User-Tasks).
    """
    try:
        saved = list(kernel.history)
        kernel.history = []
        reply = kernel.process(prompt)
        kernel.history = saved
        if reply and reply.strip().upper() != "SILENT" and "SILENT" not in reply:
            print(f"\n[{label}] {reply}\n")
            if silent:
                # Stille Benachrichtigung — NICHT sprechen
                try:
                    from tools import send_notification
                    send_notification(f"JARVIS — {label}", reply[:200])
                except Exception:
                    pass
            else:
                tts.speak(reply)
    except Exception as e:
        log.debug("Hintergrund-Task '%s' fehlgeschlagen: %s", label, e)


def _automation_loop(kernel: "JarvisKernel", tts: "TTSEngine"):
    """
    Zentraler Hintergrund-Thread — JARVIS arbeitet autonom:
    1. Geplante Tasks (schedule_task) werden zur Fälligkeit ausgeführt.
    2. System-Check alle 5 Min. (CPU, RAM, Akku, Festplatte).
    3. Kontext-Check alle 10 Min. (Wetter, Notizen, News — je nach Tageszeit).
    4. Morgen-Briefing einmalig beim Start (7–10 Uhr).
    """
    from tools import _sched_heap, _sched_lock
    import heapq as _hq

    time.sleep(30)          # kurze Wartezeit nach Start
    last_sys_check     = 0.0
    last_context_check = 0.0
    morning_done       = False   # Morgen-Briefing nur 1× pro Sitzung
    nightly_done       = False   # Abend-Zusammenfassung nur 1× pro Tag

    threading.Thread(
        target=_run_background,
        args=(kernel, tts, _PROACTIVE_SYSTEM_CHECK, "System-Startup"),
        daemon=True,
    ).start()

    # Morgen-Briefing beim ersten Start (nur zwischen 7–10 Uhr)
    hour = datetime.datetime.now().hour
    if 7 <= hour < 10:
        time.sleep(5)
        threading.Thread(
            target=_run_background,
            args=(kernel, tts, _PROACTIVE_MORNING_BRIEF, "Briefing"),
            kwargs={"silent": False},   # Morgen-Briefing darf sprechen
            daemon=True,
        ).start()
        morning_done = True

    while True:
        time.sleep(10)      # jede 10 Sekunden prüfen
        now  = time.time()
        idle = now - _last_interaction
        hour = datetime.datetime.now().hour

        # ── Geplante Tasks ───────────────────────────────────────────────────
        due = []
        with _sched_lock:
            while _sched_heap and _sched_heap[0][0] <= now:
                next_run, name, interval, action = _hq.heappop(_sched_heap)
                due.append((name, interval, action))
                _hq.heappush(_sched_heap, (now + interval, name, interval, action))

        for name, interval, action in due:
            print(f"\n  [Aufgabe] {name}: {action}")
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, f"[Automatische Aufgabe '{name}']: {action}", name),
                kwargs={"silent": False},   # User-Tasks dürfen sprechen
                daemon=True,
            ).start()

        # ── Morgen-Briefing (falls noch nicht passiert, z.B. PC erst um 9 an)
        if not morning_done and 7 <= hour < 10:
            morning_done = True
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, _PROACTIVE_MORNING_BRIEF, "Briefing"),
                kwargs={"silent": False},   # Morgen-Briefing darf sprechen
                daemon=True,
            ).start()
            continue
        # Reset für nächsten Tag
        if hour >= 11:
            morning_done = False

        # ── System-Check alle 5 Min. ─────────────────────────────────────────
        if (now - last_sys_check) >= 300:
            last_sys_check = now
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, _PROACTIVE_SYSTEM_CHECK, "System"),
                daemon=True,
            ).start()

        # ── Kontext-Check alle 10 Min. ───────────────────────────────────────
        if (now - last_context_check) >= 600:
            last_context_check = now
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, _PROACTIVE_CONTEXT_CHECK, "Kontext"),
                daemon=True,
            ).start()

        # ── Abend-Zusammenfassung einmal täglich ───────────────────────────────
        if 22 <= hour < 24 and not nightly_done:
            nightly_done = True
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, _PROACTIVE_NIGHTLY_SUMMARY, "Abend"),
                daemon=True,
            ).start()

        # Reset für nächsten Tag
        if hour < 2:
            nightly_done = False
        if hour >= 11:
            morning_done = False

        # ── Selbstreflexion alle 60 Min. (nachts 2-5 Uhr = "Wartungsfenster") ─
        if not hasattr(_automation_loop, '_last_reflect'):
            _automation_loop._last_reflect = 0.0
        if (now - _automation_loop._last_reflect) >= 3600:
            _automation_loop._last_reflect = now
            threading.Thread(
                target=_run_background,
                args=(kernel, tts, _PROACTIVE_SELF_IMPROVE, "Evolution"),
                daemon=True,
            ).start()


def _start_tray(kernel: "JarvisKernel", tts: "TTSEngine"):
    """System-Tray-Icon für den Hintergrund-Betrieb (benötigt pystray + Pillow)."""
    if not _HAS_TRAY:
        return

    def _make_icon():
        img  = _PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = _PILDraw.Draw(img)
        # Äußerer Kreis (blau)
        draw.ellipse([2, 2, 62, 62], fill=(0, 100, 200, 220))
        # Innerer Kreis (dunkel)
        draw.ellipse([14, 14, 50, 50], fill=(0, 20, 50, 240))
        # Kern (hell)
        draw.ellipse([26, 26, 38, 38], fill=(0, 200, 255, 255))
        return img

    def _on_hologram(icon, item):
        if _holo:
            _holo.open_browser()

    def _on_exit(icon, item):
        icon.stop()
        _stop_music()
        if _holo:
            _holo.stop()
        kernel.save_session()
        os._exit(0)

    icon = _pystray.Icon(
        "JARVIS",
        _make_icon(),
        "JARVIS — schläft (zweimal klatschen)",
        menu=_pystray.Menu(
            _pystray.MenuItem("Hologramm öffnen", _on_hologram),
            _pystray.MenuItem("Beenden",          _on_exit),
        ),
    )
    threading.Thread(target=icon.run, daemon=True, name="jarvis-tray").start()


def _setup_background_logging():
    """
    Wenn pythonw.exe benutzt wird, fehlt die Konsole.
    Ausgaben werden dann in eine Logdatei umgeleitet.
    """
    import io
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        log_path = Path.home() / ".jarvis" / "jarvis.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(str(log_path), "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file


def main():
    _setup_background_logging()

    print(BANNER)
    print("Initialisiere JARVIS …\n")

    # API-Schlüssel prüfen
    if not os.getenv("GROQ_API_KEY"):
        print("⚠ GROQ_API_KEY nicht gesetzt!")
        print("  → Setze ihn in y:\\Jarvis-da-Vinci\\.env:  GROQ_API_KEY=gsk_...")
        print("  → Kostenlos auf console.groq.com")
        print()

    kernel = JarvisKernel()

    # Verbindungstest
    try:
        _test = kernel.client.chat.completions.create(
            model=kernel.MODEL,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=5,
        )
        print("✓ KI   : Groq verbunden  (Llama 4 Scout)")
    except Exception as _e:
        print(f"⚠ KI   : Groq Verbindungsfehler — {_e}")
        print("  Tipp: GROQ_API_KEY prüfen")

    kernel.load_session()

    tts = TTSEngine()
    stt = STTEngine(kernel.client)

    # Hologramm + Remote-API starten
    global _holo
    if _HAS_HOLOGRAM:
        from hologram import local_ip, _PORT
        _holo = HologramDisplay()
        _holo.set_kernel(kernel, kernel.client)
        ok = _holo.start()
        if ok:
            ip = local_ip()
            print(f"✓ Hologramm: aktiv")
            print(f"✓ Netzwerk : http://{ip}:{_PORT}  (alle Geräte im WLAN)")
        else:
            print("⚠ Hologramm: nicht verfügbar")

    # System-Tray starten
    _start_tray(kernel, tts)
    if _HAS_TRAY:
        print("✓ Tray : System-Tray-Icon aktiv")

    # Automatisierungs-Thread starten (Tasks + proaktive Checks)
    threading.Thread(
        target=_automation_loop, args=(kernel, tts), daemon=True, name="jarvis-automation"
    ).start()

    print()

    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    # ── Stimm-Registrierung ───────────────────────────────────────────────────
    if arg == "enroll":
        if stt.available:
            stt.enroll()
            print("Starte JARVIS nun normal: python Main.py")
        else:
            print("⚠ Kein Mikrofon verfügbar.")
        return

    if stt.available and not _VOICE_PROFILE_PATH.exists():
        print("  ┌─────────────────────────────────────────────────────────┐")
        print("  │  Kein Stimmprofil gefunden.                             │")
        print("  │  Führe python Main.py enroll aus um deine Stimme        │")
        print("  │  zu registrieren — danach reagiert JARVIS nur auf dich. │")
        print("  └─────────────────────────────────────────────────────────┘")
        print()

    # ── Modus wählen ──────────────────────────────────────────────────────────
    if arg == "text":
        run_text_mode(kernel, tts)
    elif arg in ("voice", "sprache"):
        run_voice_mode(kernel, tts, stt)
    elif stt.available:
        # Schlaf → Aktivierung per Doppelklatschen → Wake-Word-Modus → Schlaf …
        try:
            while True:
                _run_standby()                      # blockiert bis Doppelklatschen
                run_wake_word_mode(kernel, tts, stt)  # läuft bis Exit-Phrase
        except KeyboardInterrupt:
            pass
    else:
        run_text_mode(kernel, tts)


if __name__ == "__main__":
    main()
