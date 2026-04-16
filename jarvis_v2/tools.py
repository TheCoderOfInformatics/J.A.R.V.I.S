import json
import math
import os
import platform
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List

import requests

from .config import DATA_DIR, NOTES_FILE, TASKS_FILE

_TOOL_MAP: Dict[str, Callable[..., str]] = {}
TOOL_DEFINITIONS: List[Dict[str, Any]] = []
_sched_lock = threading.Lock()


def _tool(name: str, description: str, input_schema: Dict[str, Any]):
    def decorator(fn: Callable[..., str]):
        _TOOL_MAP[name] = fn
        TOOL_DEFINITIONS.append({
            "name": name,
            "description": description,
            "input_schema": input_schema,
        })
        return fn
    return decorator


def _read_json(path: Path, default: Any):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_tool(name: str, input_data: dict) -> str:
    fn = _TOOL_MAP.get(name)
    if not fn:
        return f"Unbekanntes Tool: {name}"
    try:
        return fn(**input_data)
    except Exception as e:
        return f"Tool-Fehler in '{name}': {e}"


@_tool(
    "get_time",
    "Return the current local date and time in German.",
    {"type": "object", "properties": {}, "required": []},
)
def get_time() -> str:
    now = time.localtime()
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    return (
        f"Es ist {now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d} am "
        f"{weekdays[now.tm_wday]}, {now.tm_mday}. {months[now.tm_mon - 1]} {now.tm_year}."
    )


@_tool(
    "web_search",
    "Search the web for current information and news.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)
def web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=4))
        if not hits:
            return f"Keine Suchergebnisse gefunden für: {query}"
        lines = [f"Web-Suchergebnisse für '{query}':"]
        for item in hits[:3]:
            lines.append(f"{item.get('title','')} - {item.get('href','')}")
        return " \n".join(lines)
    except ImportError:
        try:
            response = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=10,
                headers={"User-Agent": "JARVIS-v2/1.0"},
            )
            return response.text[:800]
        except Exception as e:
            return f"Websuche fehlgeschlagen: {e}"
    except Exception as e:
        return f"Websuche fehlgeschlagen: {e}"


@_tool(
    "get_weather",
    "Get current weather and a short forecast for a city.",
    {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)
def get_weather(city: str) -> str:
    city = city.strip()
    if not city:
        return "Bitte nenne eine Stadt."
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        rep = requests.get(url, timeout=12, headers={"User-Agent": "JARVIS-v2/1.0"})
        data = rep.json()
        cur = data["current_condition"][0]
        desc = cur["weatherDesc"][0]["value"]
        temp = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        return f"Wetter in {city}: {desc}, {temp} Grad, gefühlt wie {feels} Grad."
    except Exception as e:
        return f"Wetterdaten nicht verfügbar für {city}: {e}"


@_tool(
    "get_system_info",
    "Return CPU, RAM, disk and battery status.",
    {"type": "object", "properties": {}, "required": []},
)
def get_system_info() -> str:
    try:
        import psutil
    except ImportError:
        return "psutil nicht installiert."
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("C:") if platform.system() == "Windows" else psutil.disk_usage("/")
    parts = [f"CPU {cpu:.0f} Prozent", f"RAM {ram.percent:.0f} Prozent", f"Festplatte {disk.percent:.0f} Prozent"]
    if hasattr(psutil, "sensors_battery"):
        bat = psutil.sensors_battery()
        if bat:
            state = "Laden" if bat.power_plugged else "Entladen"
            parts.append(f"Akku {bat.percent:.0f} Prozent ({state})")
    return ". ".join(parts) + "."


@_tool(
    "run_command",
    "Execute a PowerShell command on Windows.",
    {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]},
)
def run_command(command: str, timeout: int = 30) -> str:
    try:
        shell = True if platform.system() == "Windows" else False
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command] if platform.system() == "Windows" else command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        if result.returncode != 0:
            return result.stderr.strip() or result.stdout.strip() or f"Befehl fehlgeschlagen: {result.returncode}"
        return result.stdout.strip() or "OK"
    except subprocess.TimeoutExpired:
        return "Befehl hat zu lange gebraucht."
    except Exception as e:
        return f"Fehler beim Ausführen des Befehls: {e}"


@_tool(
    "take_note",
    "Save a note or reminder.",
    {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title", "content"]},
)
def take_note(title: str, content: str) -> str:
    notes = _read_json(NOTES_FILE, [])
    notes.append({"title": title.strip(), "content": content.strip(), "created": int(time.time())})
    _save_json(NOTES_FILE, notes)
    return f"Notiz gespeichert: {title.strip()}"


@_tool(
    "read_notes",
    "Read saved notes, optionally filtered.",
    {"type": "object", "properties": {"search": {"type": "string"}}, "required": []},
)
def read_notes(search: str = "") -> str:
    notes = _read_json(NOTES_FILE, [])
    if not notes:
        return "Keine Notizen vorhanden."
    if search:
        notes = [n for n in notes if search.lower() in (n.get("title","") + n.get("content","")) .lower()]
    if not notes:
        return f"Keine Notizen gefunden für '{search}'."
    lines = []
    for n in notes[-5:]:
        lines.append(f"{n.get('title','unnamed')}: {n.get('content','')}")
    return " \n".join(lines)


@_tool(
    "schedule_task",
    "Schedule a recurring task.",
    {"type": "object", "properties": {"name": {"type": "string"}, "interval_minutes": {"type": "number"}, "action": {"type": "string"}}, "required": ["name", "interval_minutes", "action"]},
)
def schedule_task(name: str, interval_minutes: float, action: str) -> str:
    tasks = _read_json(TASKS_FILE, [])
    tasks = [t for t in tasks if t.get("name") != name]
    tasks.append({"name": name.strip(), "interval_minutes": float(interval_minutes), "action": action.strip(), "created": int(time.time())})
    _save_json(TASKS_FILE, tasks)
    return f"Aufgabe '{name.strip()}' alle {interval_minutes:.0f} Minuten geplant."


@_tool(
    "list_tasks",
    "List all scheduled recurring tasks.",
    {"type": "object", "properties": {}, "required": []},
)
def list_tasks() -> str:
    tasks = _read_json(TASKS_FILE, [])
    if not tasks:
        return "Keine geplanten Aufgaben."
    lines = []
    for t in tasks:
        lines.append(f"{t['name']}: alle {t['interval_minutes']:.0f} Minuten → {t['action']}")
    return " \n".join(lines)


@_tool(
    "cancel_task",
    "Cancel a scheduled task.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)
def cancel_task(name: str) -> str:
    tasks = _read_json(TASKS_FILE, [])
    filtered = [t for t in tasks if t.get("name") != name]
    if len(filtered) == len(tasks):
        return f"Keine Aufgabe mit dem Namen '{name}' gefunden."
    _save_json(TASKS_FILE, filtered)
    return f"Aufgabe '{name}' wurde abgebrochen."


@_tool(
    "list_processes",
    "List running processes by CPU or memory.",
    {"type": "object", "properties": {"sort_by": {"type": "string"}, "top": {"type": "integer"}}, "required": []},
)
def list_processes(sort_by: str = "cpu", top: int = 10) -> str:
    top = max(1, min(20, top))
    ps_cmd = (
        "Get-Process | Sort-Object CPU -Descending" if sort_by.lower() != "memory"
        else "Get-Process | Sort-Object WorkingSet64 -Descending"
    )
    ps = (
        ps_cmd
        + " | Select-Object -First "
        + str(top)
        + " Name, Id, CPU, @{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize | Out-String"
    )
    return run_command(ps, timeout=12)


@_tool(
    "kill_process",
    "Kill a process by name or PID.",
    {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
)
def kill_process(target: str) -> str:
    if target.isdigit():
        command = f"Stop-Process -Id {target} -Force"
    else:
        command = f"Stop-Process -Name '{target}' -Force"
    return run_command(command, timeout=10)
