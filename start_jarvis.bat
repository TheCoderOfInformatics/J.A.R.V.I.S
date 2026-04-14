@echo off
cd /d y:\Jarvis-da-Vinci

REM ══════════════════════════════════════════════════
REM  JARVIS — Startskript
REM
REM  Normaler Modus (mit Konsole zum Debuggen):
REM    start_jarvis.bat
REM
REM  Hintergrund-Modus (kein Fenster, nur Tray-Icon):
REM    start_jarvis.bat bg
REM ══════════════════════════════════════════════════

IF "%1"=="bg" GOTO background

:normal
REM Mit sichtbarer Konsole — Fehler sind sichtbar
start "JARVIS" cmd /k "python Main.py"
GOTO end

:background
REM Kein Konsolenfenster — läuft komplett im Hintergrund
REM System-Tray-Icon erscheint unten rechts in der Taskleiste
start "" pythonw Main.py

:end
