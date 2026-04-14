"""
JARVIS — Tool Implementations
Alle autonomen Werkzeuge, die JARVIS verwenden kann.
"""

import os
import json
import math
import heapq
import datetime
import platform
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional, List

import psutil
import requests

# ── Shared scheduler state (wird von Main.py gelesen) ────────────────────────
_sched_heap: List[tuple] = []   # (next_run_ts, name, interval_sec, action)
_sched_lock = threading.Lock()


# ── Tool definitions (Claude API schema) ─────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "description": (
            "Search the web for current information, recent news, and facts. "
            "Use this whenever the user asks about something you may not know, "
            "current events, prices, or real-time data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather and short forecast for any city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'Berlin' or 'New York'",
                }
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_time_and_date",
        "description": "Return the current local time, date, and day of the week.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "calculate",
        "description": (
            "Evaluate a mathematical expression. "
            "Supports arithmetic, sqrt, sin, cos, log, pi, factorial, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression, e.g. 'sqrt(144)' or '(15 * 8) / 3'",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "take_note",
        "description": "Save a note or reminder to disk for later retrieval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the note"},
                "content": {"type": "string", "description": "Full note content"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "read_notes",
        "description": "Read all saved notes, optionally filtered by a keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Optional keyword to filter notes",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_system_info",
        "description": "Return current CPU usage, RAM, disk space, and battery level.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "open_application",
        "description": (
            "Open an application, a website URL, or a file on this computer. "
            "Examples: 'spotify', 'chrome', 'notepad', 'https://google.com'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "App name, URL, or file path to open",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "get_news",
        "description": "Fetch the latest news headlines on a given topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "News topic, e.g. 'AI', 'sports', 'technology', 'Weltpolitik'",
                }
            },
            "required": ["topic"],
        },
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer that beeps and prints an alert when it expires.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Duration in minutes (can be decimal)"},
                "label": {"type": "string", "description": "Optional timer label"},
            },
            "required": ["minutes"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a PowerShell command on this Windows PC and return the output. "
            "Use this for ANY system task: managing files/folders, controlling processes, "
            "querying registry, network info, sending keystrokes, automating Windows, etc. "
            "Examples: 'Get-Process', 'New-Item', 'Stop-Process', 'Get-Content', "
            "'Invoke-WebRequest', 'Start-Job', anything PowerShell can do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "PowerShell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the full text content of any file on this PC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write to"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "List all files and folders at a given directory path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: user home)"},
            },
            "required": [],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the entire screen and save it to the Desktop.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "clipboard_get",
        "description": "Read the current contents of the Windows clipboard.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "clipboard_set",
        "description": "Write text to the Windows clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a recurring automatic task. JARVIS will execute the action "
            "every interval_minutes minutes, forever, even without user input. "
            "Use this to automate anything: news briefings, system checks, reminders, "
            "monitoring tasks, file operations, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":             {"type": "string",  "description": "Unique task name"},
                "interval_minutes": {"type": "number",  "description": "How often to run (minutes)"},
                "action":           {"type": "string",  "description": "What JARVIS should do — natural language description"},
            },
            "required": ["name", "interval_minutes", "action"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List all currently scheduled recurring tasks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_task",
        "description": "Cancel and remove a scheduled recurring task by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name to cancel"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_notification",
        "description": "Show a Windows desktop notification (balloon tooltip) without speaking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":   {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification body text"},
            },
            "required": ["title", "message"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type text or press keyboard shortcuts into the active window. "
            "Special keys: {ENTER}, {TAB}, {ESC}, {UP}, {DOWN}, {F5}. "
            "Modifiers: ^=Ctrl, %=Alt, +=Shift. Example: '^c' = Ctrl+C."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text or key sequence to send"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_active_window",
        "description": "Return the title of the currently focused window.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "control_volume",
        "description": (
            "Control the Windows system volume. "
            "Actions: 'get' returns current level, 'set' sets to a percentage (0-100), "
            "'mute' mutes, 'unmute' unmutes, 'up' increases by 10%, 'down' decreases by 10%."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'get', 'set', 'mute', 'unmute', 'up', 'down'"},
                "level":  {"type": "integer", "description": "Volume level 0-100 (only for 'set')"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "media_control",
        "description": (
            "Control media playback on this PC (Spotify, YouTube, Windows Media Player, etc.). "
            "Actions: 'play_pause', 'next', 'previous', 'stop'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'play_pause', 'next', 'previous', 'stop'"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "lock_screen",
        "description": "Lock the Windows screen immediately.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "system_power",
        "description": (
            "Control system power state. "
            "Actions: 'sleep' (suspend), 'restart' (reboot), 'shutdown' (power off). "
            "WARNING: restart and shutdown will close all applications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'sleep', 'restart', or 'shutdown'"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "get_network_info",
        "description": "Get current network information: IP address, WiFi network name, connection status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_processes",
        "description": "List the top running processes sorted by CPU or memory usage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "'cpu' or 'memory' (default: cpu)"},
                "top":     {"type": "integer", "description": "How many processes to show (default: 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "kill_process",
        "description": "Terminate a running process by name or PID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Process name (e.g. 'chrome.exe') or PID"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "set_screen_brightness",
        "description": "Set the screen brightness level (0-100). Works on laptops with a display driver.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Brightness 0-100"},
            },
            "required": ["level"],
        },
    },
    {
        "name": "get_current_media",
        "description": "Get information about currently playing media (song, artist, app).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "empty_recycle_bin",
        "description": "Empty the Windows Recycle Bin.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "send_email",
        "description": "Send an email via Outlook. Requires Outlook to be installed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to":      {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body":    {"type": "string", "description": "Email body text"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "wifi_control",
        "description": (
            "Control WiFi connections. Actions: 'list' available networks, "
            "'connect' to a network (with optional password), 'disconnect', 'status'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "description": "'list', 'connect', 'disconnect', 'status'"},
                "network":  {"type": "string", "description": "Network name (for 'connect')"},
                "password": {"type": "string", "description": "WiFi password (for 'connect' to new network)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_wallpaper",
        "description": "Set the desktop wallpaper to an image file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to image file (JPG, PNG, BMP)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "move_window",
        "description": "Move and/or resize a window by its title. Use -1 for width/height to keep current size.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":  {"type": "string",  "description": "Part of the window title to find"},
                "x":      {"type": "integer", "description": "X position (pixels from left)"},
                "y":      {"type": "integer", "description": "Y position (pixels from top)"},
                "width":  {"type": "integer", "description": "Window width (-1 = keep current)"},
                "height": {"type": "integer", "description": "Window height (-1 = keep current)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "translate",
        "description": "Translate text between languages (default: English ↔ German).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text":    {"type": "string", "description": "Text to translate"},
                "to_lang": {"type": "string", "description": "Target language code: 'de', 'en', 'fr', 'es', etc. (default: de)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "define_word",
        "description": "Look up the definition of an English word.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "Word to define"},
            },
            "required": ["word"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for files by name pattern recursively. Use * as wildcard (e.g. '*.pdf', 'report*').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string",  "description": "File name pattern with wildcards"},
                "path":        {"type": "string",  "description": "Starting directory (default: user home)"},
                "max_results": {"type": "integer", "description": "Maximum results (default: 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "compress_files",
        "description": "Compress files or a folder into a ZIP archive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source":      {"type": "string", "description": "File or folder path to compress"},
                "destination": {"type": "string", "description": "Output ZIP file path"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "extract_files",
        "description": "Extract a ZIP archive to a destination folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "archive":     {"type": "string", "description": "Path to ZIP file"},
                "destination": {"type": "string", "description": "Extraction folder (default: same folder as archive)"},
            },
            "required": ["archive"],
        },
    },
    {
        "name": "download_file",
        "description": "Download a file from a URL to the local PC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":         {"type": "string", "description": "URL to download"},
                "destination": {"type": "string", "description": "Save path (default: Downloads folder)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "convert_units",
        "description": (
            "Convert between units: length (mm,cm,m,km,in,ft,mi), weight (mg,g,kg,lb,oz), "
            "temperature (C,F), currency (EUR,USD,GBP,CHF,JPY etc. — live exchange rates)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value":     {"type": "number", "description": "Numeric value to convert"},
                "from_unit": {"type": "string", "description": "Source unit (e.g. 'km', 'lb', 'EUR', 'C')"},
                "to_unit":   {"type": "string", "description": "Target unit (e.g. 'mi', 'kg', 'USD', 'F')"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
    {
        "name": "set_alarm",
        "description": "Set an alarm for a specific time (HH:MM). Beeps and shows a popup when it fires.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_str": {"type": "string", "description": "Alarm time in HH:MM format (e.g. '07:30', '22:00')"},
                "label":    {"type": "string", "description": "Alarm label (default: 'Alarm')"},
            },
            "required": ["time_str"],
        },
    },
    {
        "name": "play_music",
        "description": "Search and play music via Spotify (if running) or YouTube Music.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Song, artist, or album to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_location",
        "description": "Get the current approximate location (city, country, coordinates) based on IP.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },

    # ── Selbstverbesserung ───────────────────────────────────────────────────
    {
        "name": "add_new_tool",
        "description": (
            "Create a brand-new tool for JARVIS. Provide name, description, parameters, "
            "and Python implementation code. The tool is permanently added to tools.py "
            "and available after restart. Use this whenever you realize you need a "
            "capability you don't have yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "Tool function name (snake_case)"},
                "description": {"type": "string", "description": "What the tool does (for the AI)"},
                "parameters":  {"type": "string", "description": "JSON string of input_schema properties, e.g. '{\"query\": {\"type\": \"string\", \"description\": \"...\"}}'"},
                "required":    {"type": "string", "description": "Comma-separated required param names, e.g. 'query,limit'"},
                "code":        {"type": "string", "description": "Full Python function body (will be wrapped in @_tool decorator). Include imports inside the function."},
            },
            "required": ["name", "description", "parameters", "code"],
        },
    },
    {
        "name": "edit_own_code",
        "description": (
            "Edit JARVIS's own source code. Find a specific text in a source file "
            "and replace it. Creates a backup before every edit. "
            "Use this to fix bugs, optimize code, or improve behavior. "
            "Files: 'tools.py', 'Main.py', 'hologram.py', 'hologram.html'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file":        {"type": "string",  "description": "File to edit: 'tools.py', 'Main.py', 'hologram.py', or 'hologram.html'"},
                "find":        {"type": "string",  "description": "Exact text to find (must be unique in the file)"},
                "replace":     {"type": "string",  "description": "Text to replace it with"},
            },
            "required": ["file", "find", "replace"],
        },
    },
    {
        "name": "read_own_code",
        "description": (
            "Read JARVIS's own source code to understand, analyze, or plan improvements. "
            "Returns the content of the specified file or a section of it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file":  {"type": "string",  "description": "File to read: 'tools.py', 'Main.py', 'hologram.py', 'hologram.html'"},
                "start": {"type": "integer", "description": "Start line number (default: 1)"},
                "end":   {"type": "integer", "description": "End line number (default: 100)"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "learn",
        "description": (
            "Save a lesson, insight, or user preference that JARVIS should remember permanently. "
            "These lessons are loaded into context at every startup and shape future behavior. "
            "Use this after making mistakes, discovering user preferences, or finding better approaches."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lesson": {"type": "string", "description": "What was learned — be specific and actionable"},
                "category": {"type": "string", "description": "'user_pref', 'bug_fix', 'optimization', 'new_capability', or 'behavior'"},
            },
            "required": ["lesson"],
        },
    },
    {
        "name": "self_reflect",
        "description": (
            "Trigger a self-analysis. JARVIS reads its own code, reviews recent learnings, "
            "and returns a concrete list of improvements to make. "
            "Call this proactively to evolve."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },

    # ── Iron Man JARVIS — Erweiterte Fähigkeiten ─────────────────────────────
    {
        "name": "smart_home",
        "description": (
            "Control smart home devices (lights, thermostat, plugs). "
            "Actions: 'lights_on', 'lights_off', 'lights_dim <0-100>', 'lights_color <hex>', "
            "'thermostat <temp_celsius>', 'plug_on <name>', 'plug_off <name>', 'status'. "
            "Works via PowerShell COM/REST for Philips Hue, MQTT, or generic APIs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Smart home action"},
                "target": {"type": "string", "description": "Device name or group (default: all)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "security_scan",
        "description": (
            "Run a security assessment of the PC. Checks: open ports, firewall status, "
            "Windows Defender status, recent login attempts, suspicious processes, "
            "network connections to unusual IPs. Like JARVIS scanning for threats."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'quick' (30s), 'full' (2min), or 'network' (scan local network)"},
            },
            "required": [],
        },
    },
    {
        "name": "get_route",
        "description": "Get driving/walking directions and travel time between two locations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin":      {"type": "string", "description": "Starting location"},
                "destination": {"type": "string", "description": "Destination location"},
                "mode":        {"type": "string", "description": "'driving', 'walking', 'transit' (default: driving)"},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "get_stock",
        "description": "Get current stock/crypto price and daily change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker (AAPL, TSLA) or crypto (bitcoin, ethereum)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "research",
        "description": (
            "Deep research mode: compile a comprehensive briefing on any topic. "
            "Searches multiple sources, synthesizes findings, returns a structured report. "
            "Like JARVIS compiling a dossier for Tony."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":  {"type": "string", "description": "Research topic"},
                "depth":  {"type": "string", "description": "'brief' (3 sources), 'standard' (5), 'deep' (10)"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "system_diagnostics",
        "description": (
            "Full system diagnostic — like JARVIS running a suit diagnostic. "
            "Checks: CPU/GPU temp, disk health (SMART), driver issues, Windows updates, "
            "startup programs, memory leaks, uptime, event log errors."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "manage_contacts",
        "description": (
            "Manage JARVIS's contact database. Actions: 'add' a contact, 'find' by name, "
            "'list' all contacts, 'remove' a contact. Stores name, phone, email, notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'find', 'list', 'remove'"},
                "name":   {"type": "string", "description": "Contact name"},
                "phone":  {"type": "string", "description": "Phone number (for 'add')"},
                "email":  {"type": "string", "description": "Email address (for 'add')"},
                "notes":  {"type": "string", "description": "Additional notes (for 'add')"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "summarize_text",
        "description": "Summarize a long text, article, or file contents into key points.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text":   {"type": "string", "description": "Text to summarize (or file path to read)"},
                "length": {"type": "string", "description": "'short' (1-2 sentences), 'medium' (paragraph), 'detailed' (bullet points)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "generate_code",
        "description": (
            "Generate, run, and test code. Specify language and task. "
            "JARVIS writes the code, saves it, and optionally executes it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "language":    {"type": "string", "description": "'python', 'powershell', 'javascript', 'batch'"},
                "task":        {"type": "string", "description": "What the code should do"},
                "execute":     {"type": "boolean", "description": "Run the code after generating (default: false)"},
                "filename":    {"type": "string", "description": "Save as filename (default: auto-generated)"},
            },
            "required": ["language", "task"],
        },
    },
    {
        "name": "screen_ocr",
        "description": "Take a screenshot and extract all text from it using OCR. Like JARVIS reading a display.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dictate",
        "description": (
            "Type a long dictated text into the active window. "
            "Unlike type_text (which sends key events), this uses clipboard paste for reliability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Full text to paste into active window"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "manage_startup",
        "description": "Manage Windows startup programs. Actions: 'list', 'add <path>', 'remove <name>'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'list', 'add', 'remove'"},
                "target": {"type": "string", "description": "Program path (add) or name (remove)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "system_cleanup",
        "description": (
            "Clean up the system: temp files, browser cache, Windows update cache, "
            "old log files. Frees disk space. Like JARVIS optimizing the suit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'temp' (safe), 'full' (aggressive), 'preview' (show what would be deleted)"},
            },
            "required": [],
        },
    },
]


# ── Tool dispatcher ───────────────────────────────────────────────────────────

_TOOL_MAP: dict = {}


def _tool(name: str):
    """Decorator that registers a function as a named tool."""
    def decorator(fn):
        _TOOL_MAP[name] = fn
        return fn
    return decorator


def execute_tool(name: str, input_data: dict) -> str:
    """Dispatch a tool call by name and return its string result."""
    fn = _TOOL_MAP.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return fn(**input_data)
    except TypeError as e:
        return f"Parameter error in '{name}': {e}"
    except Exception as e:
        return f"Tool error in '{name}': {e}"


# ── Implementations ───────────────────────────────────────────────────────────

@_tool("web_search")
def web_search(query: str) -> str:
    """Search using duckduckgo-search; falls back to DuckDuckGo Instant API."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=5))
        if not hits:
            return f"No results found for: {query}"
        lines = [f"Web search results for '{query}':\n"]
        for i, h in enumerate(hits[:4], 1):
            lines.append(
                f"{i}. {h.get('title', '')}\n"
                f"   {h.get('body', '')[:200]}\n"
                f"   {h.get('href', '')}"
            )
        return "\n".join(lines)
    except ImportError:
        return _ddg_instant(query)
    except Exception as e:
        return f"Search failed: {e}"


def _ddg_instant(query: str) -> str:
    """Fallback: DuckDuckGo Instant Answer API (no library required)."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=8,
            headers={"User-Agent": "JARVIS-Assistant/2.0"},
        )
        d = r.json()
        parts = []
        if d.get("AbstractText"):
            parts.append(d["AbstractText"])
        for t in d.get("RelatedTopics", [])[:4]:
            if isinstance(t, dict) and t.get("Text"):
                parts.append(t["Text"])
        return "\n\n".join(parts) if parts else f"No information found for: {query}"
    except Exception as e:
        return f"Search unavailable: {e}"


@_tool("get_weather")
def get_weather(city: str) -> str:
    try:
        r = requests.get(
            f"https://wttr.in/{urllib.parse.quote(city)}?format=j1",
            timeout=8,
            headers={"User-Agent": "JARVIS-Assistant/2.0"},
        )
        d = r.json()
        cur = d["current_condition"][0]
        desc     = cur["weatherDesc"][0]["value"]
        temp_c   = int(cur["temp_C"])
        feels_c  = int(cur["FeelsLikeC"])
        humidity = cur["humidity"]
        wind     = int(cur["windspeedKmph"])

        result = (
            f"Weather in {city}: {desc}. "
            f"{temp_c} degrees Celsius, feels like {feels_c}. "
            f"Humidity {humidity} percent. Wind {wind} km/h."
        )

        weather_days = d.get("weather", [])
        if len(weather_days) > 1:
            t   = weather_days[1]
            hi  = t.get("maxtempC", "?")
            lo  = t.get("mintempC", "?")
            result += f" Tomorrow: {lo} to {hi} degrees."

        return result
    except Exception as e:
        return f"Weather unavailable for {city}: {e}"


@_tool("get_time_and_date")
def get_time_and_date() -> str:
    now = datetime.datetime.now()
    return (
        f"It is {now.strftime('%H:%M:%S')} on "
        f"{now.strftime('%A, %d. %B %Y')}."
    )


@_tool("calculate")
def calculate(expression: str) -> str:
    safe_ns: dict = {
        "__builtins__": None,
        **{k: getattr(math, k) for k in dir(math) if not k.startswith("_")},
        "abs": abs, "round": round, "int": int, "float": float,
        "max": max, "min": min, "sum": sum, "pow": pow,
    }
    try:
        result = eval(expression, safe_ns)  # noqa: S307
        if isinstance(result, float):
            result = round(result, 10)
            if result == int(result):
                result = int(result)
        return f"{expression} = {result}"
    except ZeroDivisionError:
        return "Error: division by zero."
    except Exception as e:
        return f"Cannot evaluate '{expression}': {e}"


# ── Notes storage ─────────────────────────────────────────────────────────────

_NOTES_PATH = Path.home() / ".jarvis" / "notes.json"


def _notes_load() -> list:
    _NOTES_PATH.parent.mkdir(exist_ok=True)
    if not _NOTES_PATH.exists():
        return []
    try:
        return json.loads(_NOTES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _notes_save(notes: list) -> None:
    _NOTES_PATH.parent.mkdir(exist_ok=True)
    _NOTES_PATH.write_text(
        json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8"
    )


@_tool("take_note")
def take_note(title: str, content: str) -> str:
    notes = _notes_load()
    notes.append(
        {
            "id": len(notes) + 1,
            "title": title,
            "content": content,
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    )
    _notes_save(notes)
    return f"Note '{title}' saved. You now have {len(notes)} note(s)."


@_tool("read_notes")
def read_notes(search: str = "") -> str:
    notes = _notes_load()
    if not notes:
        return "No notes saved yet."
    if search:
        notes = [
            n for n in notes
            if search.lower() in n["title"].lower()
            or search.lower() in n["content"].lower()
        ]
        if not notes:
            return f"No notes matching '{search}'."
    recent = notes[-10:]
    lines = [f"{len(notes)} note(s) found:"]
    for n in recent:
        lines.append(f"\n[{n['ts']}] #{n['id']}: {n['title']}\n  {n['content']}")
    return "\n".join(lines)


@_tool("get_system_info")
def get_system_info() -> str:
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    parts = [
        f"CPU {cpu}%",
        f"RAM {ram.percent}%  "
        f"({ram.used // (1024 ** 3):.1f} of {ram.total // (1024 ** 3):.1f} GB used)",
    ]
    try:
        root = "C:" if platform.system() == "Windows" else "/"
        disk = psutil.disk_usage(root)
        parts.append(
            f"Disk {disk.percent}%  "
            f"({disk.used // (1024 ** 3):.0f}/{disk.total // (1024 ** 3):.0f} GB)"
        )
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        if bat:
            state = "charging" if bat.power_plugged else "discharging"
            parts.append(f"Battery {bat.percent:.0f}%  ({state})")
    except Exception:
        pass
    return ".  ".join(parts) + "."


_WIN_APPS = {
    "notepad": "notepad.exe",
    "editor":  "notepad.exe",
    "calculator": "calc.exe",
    "calc":    "calc.exe",
    "paint":   "mspaint.exe",
    "explorer":     "explorer.exe",
    "file manager": "explorer.exe",
    "cmd":            "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "chrome":        "chrome",
    "google chrome": "chrome",
    "firefox":       "firefox",
    "edge":            "msedge",
    "microsoft edge":  "msedge",
    "spotify":   "spotify",
    "discord":   "discord",
    "vscode":              "code",
    "visual studio code":  "code",
    "word":      "winword",
    "excel":     "excel",
    "powerpoint":"powerpnt",
    "outlook":   "outlook",
    "teams":     "msteams",
    "zoom":      "zoom",
    "steam":     "steam",
    "vlc":       "vlc",
    "obs":       "obs64",
}


@_tool("open_application")
def open_application(target: str) -> str:
    # Is it a URL?
    if target.startswith(("http://", "https://", "www.")):
        url = target if target.startswith("http") else "https://" + target
        import webbrowser
        webbrowser.open(url)
        return f"Opening {url} in the browser."

    cmd = _WIN_APPS.get(target.lower().strip(), target)
    try:
        if platform.system() == "Windows":
            try:
                os.startfile(cmd)
            except Exception:
                subprocess.Popen(
                    cmd, shell=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", cmd])
        else:
            subprocess.Popen([cmd])
        return f"Opening {target}."
    except Exception as e:
        return f"Could not open '{target}': {e}"


@_tool("get_news")
def get_news(topic: str) -> str:
    return web_search(f"{topic} latest news today")


@_tool("set_timer")
def set_timer(minutes: float, label: str = "Timer") -> str:
    seconds = int(minutes * 60)

    def _fire():
        import time as _t
        _t.sleep(seconds)
        print(f"\n\a⏰  TIMER: '{label}'  —  {minutes} Minute(n) um!\n")
        if platform.system() == "Windows":
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1000, 400)
                    _t.sleep(0.2)
            except Exception:
                pass

    threading.Thread(target=_fire, daemon=True).start()

    m, s = divmod(seconds, 60)
    dur = f"{m} minute{'s' if m != 1 else ''}"
    if s:
        dur += f" and {s} second{'s' if s != 1 else ''}"
    return f"Timer '{label}' set for {dur}. I will alert you when it's done."


# ── System control ────────────────────────────────────────────────────────────

@_tool("run_command")
def run_command(command: str, timeout: int = 30) -> str:
    """Execute a PowerShell command and return its output."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:
            return out[:3000]
        if err:
            return f"Error: {err[:1000]}"
        return "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Command failed: {e}"


@_tool("read_file")
def read_file(path: str) -> str:
    """Read a file's text content."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"File not found: {path}"
    if p.stat().st_size > 200_000:
        return f"File too large ({p.stat().st_size // 1024} KB). Use run_command to read specific lines."
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Cannot read file: {e}"


@_tool("write_file")
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates parent directories if needed)."""
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} characters to {p}."
    except Exception as e:
        return f"Cannot write file: {e}"


@_tool("list_files")
def list_files(path: str = "~") -> str:
    """List files and directories at the given path."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"Path not found: {path}"
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        lines = []
        for e in entries[:60]:
            if e.is_dir():
                lines.append(f"[DIR]  {e.name}/")
            else:
                kb = e.stat().st_size / 1024
                lines.append(f"[FILE] {e.name}  ({kb:.1f} KB)")
        if len(list(p.iterdir())) > 60:
            lines.append("… (truncated)")
        return "\n".join(lines) if lines else "(empty)"
    except PermissionError:
        return f"Access denied: {path}"


@_tool("take_screenshot")
def take_screenshot() -> str:
    """Take a full screenshot and save to Desktop."""
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path.home() / "Desktop" / f"jarvis_{ts}.png"
    ps   = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "$bmp = [System.Drawing.Bitmap]::new($s.Width,$s.Height); "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($s.Location,[System.Drawing.Point]::Empty,$s.Size); "
        f"$bmp.Save('{dest}'); $g.Dispose(); $bmp.Dispose()"
    )
    result = run_command(ps, timeout=10)
    if "Error" in result or "failed" in result.lower():
        return f"Screenshot failed: {result}"
    return f"Screenshot saved to {dest}."


@_tool("clipboard_get")
def clipboard_get() -> str:
    """Read the current clipboard contents."""
    result = run_command("Get-Clipboard", timeout=5)
    return result if result != "(no output)" else "(clipboard is empty)"


@_tool("clipboard_set")
def clipboard_set(text: str) -> str:
    """Write text to the clipboard."""
    escaped = text.replace("'", "''")
    run_command(f"Set-Clipboard -Value '{escaped}'", timeout=5)
    return f"Clipboard set ({len(text)} characters)."


# ── Task scheduler ────────────────────────────────────────────────────────────

@_tool("schedule_task")
def schedule_task(name: str, interval_minutes: float, action: str) -> str:
    """Schedule a recurring task."""
    interval_sec = interval_minutes * 60
    next_run     = time.time() + interval_sec
    with _sched_lock:
        # Alten Task gleichen Namens entfernen
        _sched_heap[:] = [(t, n, i, a) for t, n, i, a in _sched_heap if n != name]
        heapq.heappush(_sched_heap, (next_run, name, interval_sec, action))
    m = int(interval_minutes)
    s = int((interval_minutes - m) * 60)
    dur = f"{m} Min." + (f" {s} Sek." if s else "")
    return f"Aufgabe '{name}' geplant: alle {dur} — {action}"


@_tool("list_tasks")
def list_tasks() -> str:
    """List all scheduled tasks."""
    with _sched_lock:
        if not _sched_heap:
            return "Keine geplanten Aufgaben."
        lines = ["Geplante Aufgaben:"]
        for next_run, name, interval_sec, action in sorted(_sched_heap):
            in_sec  = max(0, int(next_run - time.time()))
            in_min  = in_sec // 60
            every   = int(interval_sec // 60)
            lines.append(
                f"  • {name}  (alle {every} Min., nächste Ausführung in {in_min} Min.)\n"
                f"    → {action}"
            )
        return "\n".join(lines)


@_tool("cancel_task")
def cancel_task(name: str) -> str:
    """Cancel a scheduled task."""
    with _sched_lock:
        before = len(_sched_heap)
        _sched_heap[:] = [(t, n, i, a) for t, n, i, a in _sched_heap if n != name]
        heapq.heapify(_sched_heap)
    if len(_sched_heap) < before:
        return f"Aufgabe '{name}' wurde abgebrochen."
    return f"Keine Aufgabe mit dem Namen '{name}' gefunden."


# ── Benachrichtigungen & UI-Automatisierung ───────────────────────────────────

@_tool("send_notification")
def send_notification(title: str, message: str) -> str:
    """Show a Windows desktop balloon notification."""
    t = title.replace("'", "''")
    m = message.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(6000, '{t}', '{m}', [System.Windows.Forms.ToolTipIcon]::Info); "
        "Start-Sleep -Milliseconds 6500; $n.Dispose()"
    )
    threading.Thread(target=run_command, args=(ps, 10), daemon=True).start()
    return f"Benachrichtigung gesendet: {title}"


@_tool("type_text")
def type_text(text: str) -> str:
    """Send keyboard input to the active window."""
    escaped = (text
        .replace("'", "''")
        .replace("`", "``")
    )
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.SendKeys]::SendWait('{escaped}')"
    )
    run_command(ps, timeout=8)
    return f"Eingabe gesendet: {text[:80]}"


@_tool("get_active_window")
def get_active_window() -> str:
    """Return the title of the currently focused window."""
    ps = (
        "Add-Type @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public class W { [DllImport(\"user32\")] public static extern IntPtr GetForegroundWindow();\n"
        "[DllImport(\"user32\")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder b, int n); }\n"
        "'@\n"
        "$h = [W]::GetForegroundWindow(); $b = New-Object System.Text.StringBuilder 256; "
        "[W]::GetWindowText($h, $b, 256) | Out-Null; $b.ToString()"
    )
    return run_command(ps, timeout=5) or "(unknown)"


# ── Volume & Media ────────────────────────────────────────────────────────────

@_tool("control_volume")
def control_volume(action: str, level: int = -1) -> str:
    """Control Windows system volume."""
    action = action.lower().strip()
    if action == "get":
        ps = (
            "Add-Type -TypeDefinition @'\n"
            "using System.Runtime.InteropServices;\n"
            "[Guid(\"5CDF2C82-841E-4546-9722-0CF74078229A\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
            "interface IAudioEndpointVolume { void a(); void b(); void c(); void d();\n"
            "  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\n"
            "  int GetMasterVolumeLevelScalar(out float pfLevel); }\n"
            "'@ -PassThru 2>$null | Out-Null\n"
            "(Get-WmiObject -Class Win32_Volume | Where-Object {$_.DriveType -eq 3}).Capacity | Out-Null\n"
        )
        # Simpler PowerShell approach
        result = run_command(
            "[math]::Round((Get-WmiObject -Query 'SELECT * FROM Win32_SoundDevice').StatusInfo)",
            timeout=5
        )
        # Use nircmd or wscript as fallback
        result2 = run_command(
            "$vol = (Get-WmiObject -Namespace root/cimv2 -Class Win32_SoundDevice | Select-Object StatusInfo); "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[System.Console]::WriteLine('Volume control available')",
            timeout=5
        )
        return "Volume control: use 'set', 'up', 'down', 'mute', or 'unmute' to adjust."

    elif action == "mute":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$wsh = New-Object -com wscript.shell; "
            "$wsh.SendKeys([char]173)"  # mute key
        )
        run_command(ps, timeout=5)
        return "Microphone/audio muted."

    elif action == "unmute":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$wsh = New-Object -com wscript.shell; "
            "$wsh.SendKeys([char]173)"
        )
        run_command(ps, timeout=5)
        return "Audio unmuted."

    elif action == "up":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$wsh = New-Object -com wscript.shell; "
            "for($i=0;$i-lt5;$i++){$wsh.SendKeys([char]175);Start-Sleep -Milliseconds 50}"
        )
        run_command(ps, timeout=5)
        return "Volume increased."

    elif action == "down":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$wsh = New-Object -com wscript.shell; "
            "for($i=0;$i-lt5;$i++){$wsh.SendKeys([char]174);Start-Sleep -Milliseconds 50}"
        )
        run_command(ps, timeout=5)
        return "Volume decreased."

    elif action == "set" and 0 <= level <= 100:
        # Use nircmd if available, otherwise WScript
        steps_from_zero = level // 2  # rough approximation (each step ~2%)
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$wsh = New-Object -com wscript.shell; "
            # First mute then unmute to reset, then set volume
            f"for($i=0;$i-lt50;$i++){{$wsh.SendKeys([char]174);Start-Sleep -Milliseconds 20}}; "
            f"for($i=0;$i-lt{steps_from_zero};$i++){{$wsh.SendKeys([char]175);Start-Sleep -Milliseconds 20}}"
        )
        run_command(ps, timeout=15)
        return f"Volume set to approximately {level}%."

    return f"Unknown volume action: {action}"


@_tool("media_control")
def media_control(action: str) -> str:
    """Control media playback via Windows media keys."""
    action = action.lower().strip()
    key_map = {
        "play_pause": "179",  # VK_MEDIA_PLAY_PAUSE
        "next":       "176",  # VK_MEDIA_NEXT_TRACK
        "previous":   "177",  # VK_MEDIA_PREV_TRACK
        "stop":       "178",  # VK_MEDIA_STOP
    }
    if action not in key_map:
        return f"Unknown action '{action}'. Use: play_pause, next, previous, stop."
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"$wsh = New-Object -com wscript.shell; $wsh.SendKeys([char]{key_map[action]})"
    )
    run_command(ps, timeout=5)
    labels = {"play_pause": "Play/Pause toggled", "next": "Next track",
              "previous":   "Previous track",     "stop": "Playback stopped"}
    return labels[action] + "."


@_tool("lock_screen")
def lock_screen() -> str:
    """Lock the Windows screen."""
    run_command("rundll32.exe user32.dll,LockWorkStation", timeout=5)
    return "Screen locked."


@_tool("system_power")
def system_power(action: str) -> str:
    """Control system power state."""
    action = action.lower().strip()
    if action == "sleep":
        run_command("Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)", timeout=8)
        return "System going to sleep."
    elif action == "restart":
        run_command("shutdown /r /t 30 /c 'JARVIS initiiert Neustart in 30 Sekunden'", timeout=5)
        return "Restart scheduled in 30 seconds. Run 'shutdown /a' to abort."
    elif action == "shutdown":
        run_command("shutdown /s /t 30 /c 'JARVIS initiiert Herunterfahren in 30 Sekunden'", timeout=5)
        return "Shutdown scheduled in 30 seconds. Run 'shutdown /a' to abort."
    return f"Unknown power action: {action}. Use: sleep, restart, shutdown."


@_tool("get_network_info")
def get_network_info() -> str:
    """Get network information."""
    ps = (
        "$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike '*Loopback*'} | Select-Object -First 1).IPAddress; "
        "$wifi = (netsh wlan show interfaces | Select-String 'SSID' | Select-Object -First 1) -replace '.*SSID.*: ',''; "
        "$ext = (Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing -TimeoutSec 5).Content; "
        "Write-Output \"Local IP: $ip | WiFi: $wifi | Public IP: $ext\""
    )
    result = run_command(ps, timeout=12)
    return result or "Network info unavailable."


@_tool("list_processes")
def list_processes(sort_by: str = "cpu", top: int = 10) -> str:
    """List top running processes."""
    top = min(top, 20)
    if sort_by.lower() == "memory":
        ps = f"Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First {top} Name, Id, @{{n='CPU';e={{[math]::Round($_.CPU,1)}}}}, @{{n='RAM_MB';e={{[math]::Round($_.WorkingSet64/1MB,1)}}}} | Format-Table -AutoSize | Out-String"
    else:
        ps = f"Get-Process | Sort-Object CPU -Descending | Select-Object -First {top} Name, Id, @{{n='CPU';e={{[math]::Round($_.CPU,1)}}}}, @{{n='RAM_MB';e={{[math]::Round($_.WorkingSet64/1MB,1)}}}} | Format-Table -AutoSize | Out-String"
    return run_command(ps, timeout=10)


@_tool("kill_process")
def kill_process(target: str) -> str:
    """Kill a process by name or PID."""
    target = target.strip()
    if target.isdigit():
        ps = f"Stop-Process -Id {target} -Force -ErrorAction SilentlyContinue; Write-Output 'Process {target} terminated.'"
    else:
        name = target.replace("'", "''")
        ps = f"Stop-Process -Name '{name}' -Force -ErrorAction SilentlyContinue; Write-Output 'Process {name} terminated.'"
    return run_command(ps, timeout=8)


@_tool("set_screen_brightness")
def set_screen_brightness(level: int) -> str:
    """Set screen brightness (0-100). Works on most laptops."""
    level = max(0, min(100, int(level)))
    ps = (
        f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level}); "
        f"Write-Output 'Brightness set to {level}%'"
    )
    result = run_command(ps, timeout=8)
    if "Error" in result or "not find" in result.lower():
        return f"Could not set brightness: {result[:200]}"
    return f"Screen brightness set to {level}%."


@_tool("get_current_media")
def get_current_media() -> str:
    """Get currently playing media info via Windows."""
    ps = (
        "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
        "Where-Object {$_.Name -in 'Spotify','chrome','msedge','firefox','vlc','wmplayer','Music'} | "
        "Select-Object Name, MainWindowTitle | Format-Table -AutoSize | Out-String"
    )
    result = run_command(ps, timeout=8)
    if not result or result.strip() in ["", "(no output)"]:
        return "No media player with active content detected."
    return result


@_tool("empty_recycle_bin")
def empty_recycle_bin() -> str:
    """Empty the Windows Recycle Bin."""
    ps = "Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Output 'Recycle Bin emptied.'"
    return run_command(ps, timeout=10)


# ── E-Mail (Outlook / Windows Mail) ─────────────────────────────────────────

@_tool("send_email")
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via the default Windows mail client (Outlook COM)."""
    to_e = to.replace("'", "''")
    subj  = subject.replace("'", "''")
    bd    = body.replace("'", "''").replace("\n", "`r`n")
    ps = (
        "$ol = New-Object -ComObject Outlook.Application; "
        "$m = $ol.CreateItem(0); "
        f"$m.To = '{to_e}'; $m.Subject = '{subj}'; $m.Body = '{bd}'; "
        "$m.Send(); Write-Output 'Email sent.'"
    )
    result = run_command(ps, timeout=15)
    if "Error" in result or "failed" in result.lower():
        return f"Email senden fehlgeschlagen: {result[:300]}"
    return f"E-Mail an {to} gesendet: {subject}"


# ── WiFi / Bluetooth ────────────────────────────────────────────────────────

@_tool("wifi_control")
def wifi_control(action: str, network: str = "", password: str = "") -> str:
    """Control WiFi: 'list' available networks, 'connect' to a network, 'disconnect', 'status'."""
    action = action.lower().strip()
    if action == "list":
        return run_command("netsh wlan show networks mode=bssid | Select-String 'SSID|Signal' | Out-String", timeout=10)
    elif action == "status":
        return run_command("netsh wlan show interfaces | Out-String", timeout=8)
    elif action == "disconnect":
        return run_command("netsh wlan disconnect", timeout=5)
    elif action == "connect" and network:
        net = network.replace("'", "''")
        if password:
            # Profil erstellen + verbinden
            pw = password.replace("'", "''")
            xml = (
                f'<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                f'<name>{net}</name><SSIDConfig><SSID><name>{net}</name></SSID></SSIDConfig>'
                f'<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
                f'<MSM><security><authEncryption><authentication>WPA2PSK</authentication>'
                f'<encryption>AES</encryption></authEncryption>'
                f'<sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
                f'<keyMaterial>{pw}</keyMaterial></sharedKey></security></MSM></WLANProfile>'
            )
            xml_e = xml.replace("'", "''")
            ps = (
                f"$xml = '{xml_e}'; "
                "$tmp = \"$env:TEMP\\wifi_profile.xml\"; "
                "$xml | Out-File -Encoding utf8 $tmp; "
                "netsh wlan add profile filename=$tmp; "
                f"netsh wlan connect name='{net}'; "
                "Remove-Item $tmp"
            )
            return run_command(ps, timeout=12)
        return run_command(f"netsh wlan connect name='{network.replace(chr(39), chr(39)+chr(39))}'", timeout=8)
    return f"Unbekannte WiFi-Aktion: {action}. Nutze: list, connect, disconnect, status."


# ── Bildschirm / Display ────────────────────────────────────────────────────

@_tool("set_wallpaper")
def set_wallpaper(path: str) -> str:
    """Set the desktop wallpaper to an image file."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"Datei nicht gefunden: {path}"
    abs_path = str(p.resolve()).replace("'", "''")
    ps = (
        "Add-Type @'\n"
        "using System.Runtime.InteropServices;\n"
        "public class Wallpaper { [DllImport(\"user32.dll\", CharSet=CharSet.Auto)]\n"
        "public static extern int SystemParametersInfo(int a, int b, string c, int d); }\n"
        "'@\n"
        f"[Wallpaper]::SystemParametersInfo(0x0014, 0, '{abs_path}', 0x01 -bor 0x02)"
    )
    run_command(ps, timeout=8)
    return f"Wallpaper gesetzt: {p.name}"


@_tool("move_window")
def move_window(title: str, x: int = 0, y: int = 0, width: int = -1, height: int = -1) -> str:
    """Move and resize a window by its title. Use -1 for width/height to keep current size."""
    title_e = title.replace("'", "''")
    ps = (
        "Add-Type @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public class WinAPI {\n"
        "  [DllImport(\"user32.dll\")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int h2, bool r);\n"
        "  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr h, out RECT r);\n"
        "  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }\n"
        "}\n'@\n"
        f"$p = Get-Process | Where-Object {{$_.MainWindowTitle -like '*{title_e}*'}} | Select-Object -First 1; "
        "if($p) { "
        "$h = $p.MainWindowHandle; "
        "$r = New-Object WinAPI+RECT; [WinAPI]::GetWindowRect($h, [ref]$r) | Out-Null; "
        f"$w = if({width} -gt 0){{{width}}}else{{$r.R-$r.L}}; "
        f"$h2 = if({height} -gt 0){{{height}}}else{{$r.B-$r.T}}; "
        f"[WinAPI]::MoveWindow($h, {x}, {y}, $w, $h2, $true); "
        "'Window moved.' } else { 'Window not found.' }"
    )
    return run_command(ps, timeout=8)


# ── Zwischenablage-Verlauf & Textverarbeitung ────────────────────────────────

@_tool("translate")
def translate(text: str, to_lang: str = "de") -> str:
    """Translate text using MyMemory API (free, no key needed)."""
    from_lang = "en" if to_lang == "de" else "de"
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": f"{from_lang}|{to_lang}"},
            timeout=8,
        )
        data = r.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        return translated if translated else f"Übersetzung fehlgeschlagen."
    except Exception as e:
        return f"Übersetzungs-Fehler: {e}"


@_tool("define_word")
def define_word(word: str) -> str:
    """Look up the definition of a word using the Free Dictionary API."""
    try:
        r = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}", timeout=8)
        if r.status_code != 200:
            return f"Kein Eintrag gefunden für '{word}'."
        data = r.json()
        defs = []
        for meaning in data[0].get("meanings", [])[:3]:
            pos = meaning.get("partOfSpeech", "")
            for d in meaning.get("definitions", [])[:2]:
                defs.append(f"  [{pos}] {d['definition']}")
        return f"'{word}':\n" + "\n".join(defs) if defs else f"Keine Definition für '{word}'."
    except Exception as e:
        return f"Wörterbuch-Fehler: {e}"


# ── Dateisystem erweitert ────────────────────────────────────────────────────

@_tool("search_files")
def search_files(query: str, path: str = "~", max_results: int = 20) -> str:
    """Search for files by name pattern recursively. Use * as wildcard."""
    p = Path(path).expanduser()
    query_e = query.replace("'", "''")
    ps = (
        f"Get-ChildItem -Path '{str(p)}' -Recurse -Filter '{query_e}' "
        f"-ErrorAction SilentlyContinue | Select-Object -First {max_results} FullName, Length, LastWriteTime | "
        "Format-Table -AutoSize | Out-String"
    )
    result = run_command(ps, timeout=20)
    return result if result and result.strip() != "(no output)" else f"Keine Dateien gefunden: {query}"


@_tool("compress_files")
def compress_files(source: str, destination: str) -> str:
    """Compress files/folder into a ZIP archive."""
    src = Path(source).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if not src.exists():
        return f"Quelle nicht gefunden: {source}"
    ps = f"Compress-Archive -Path '{src}' -DestinationPath '{dst}' -Force; Write-Output 'Archiv erstellt: {dst.name}'"
    return run_command(ps, timeout=30)


@_tool("extract_files")
def extract_files(archive: str, destination: str = "") -> str:
    """Extract a ZIP archive."""
    arc = Path(archive).expanduser().resolve()
    if not arc.exists():
        return f"Archiv nicht gefunden: {archive}"
    dst = Path(destination).expanduser().resolve() if destination else arc.parent / arc.stem
    ps = f"Expand-Archive -Path '{arc}' -DestinationPath '{dst}' -Force; Write-Output 'Entpackt nach: {dst}'"
    return run_command(ps, timeout=30)


# ── Download ─────────────────────────────────────────────────────────────────

@_tool("download_file")
def download_file(url: str, destination: str = "") -> str:
    """Download a file from a URL to the specified path (default: Downloads folder)."""
    if not destination:
        filename = url.split("/")[-1].split("?")[0] or "download"
        destination = str(Path.home() / "Downloads" / filename)
    dst = Path(destination).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, timeout=30, stream=True,
                         headers={"User-Agent": "JARVIS-Assistant/2.0"})
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size_kb = dst.stat().st_size / 1024
        return f"Download abgeschlossen: {dst} ({size_kb:.1f} KB)"
    except Exception as e:
        return f"Download fehlgeschlagen: {e}"


# ── Konvertierungen & Einheiten ──────────────────────────────────────────────

@_tool("convert_units")
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between common units (length, weight, temperature, currency via API)."""
    from_u = from_unit.lower().strip()
    to_u   = to_unit.lower().strip()

    # Temperatur
    if from_u in ("c", "celsius") and to_u in ("f", "fahrenheit"):
        return f"{value} °C = {value * 9/5 + 32:.1f} °F"
    if from_u in ("f", "fahrenheit") and to_u in ("c", "celsius"):
        return f"{value} °F = {(value - 32) * 5/9:.1f} °C"

    # Länge (m als Basis)
    length = {
        "mm": 0.001, "cm": 0.01, "m": 1, "km": 1000,
        "in": 0.0254, "inch": 0.0254, "ft": 0.3048, "feet": 0.3048, "foot": 0.3048,
        "yd": 0.9144, "yard": 0.9144, "mi": 1609.34, "mile": 1609.34, "miles": 1609.34,
    }
    if from_u in length and to_u in length:
        result = value * length[from_u] / length[to_u]
        return f"{value} {from_unit} = {result:.4g} {to_unit}"

    # Gewicht (kg als Basis)
    weight = {
        "mg": 0.000001, "g": 0.001, "kg": 1, "t": 1000, "tonne": 1000,
        "oz": 0.0283495, "lb": 0.453592, "lbs": 0.453592, "pound": 0.453592,
        "stone": 6.35029,
    }
    if from_u in weight and to_u in weight:
        result = value * weight[from_u] / weight[to_u]
        return f"{value} {from_unit} = {result:.4g} {to_unit}"

    # Währung (API)
    currencies = {"eur", "usd", "gbp", "chf", "jpy", "cny", "cad", "aud",
                  "krw", "inr", "brl", "rub", "try", "sek", "nok", "dkk", "pln", "czk"}
    if from_u in currencies and to_u in currencies:
        try:
            r = requests.get(f"https://open.er-api.com/v6/latest/{from_u.upper()}", timeout=8)
            rates = r.json().get("rates", {})
            rate = rates.get(to_u.upper())
            if rate:
                return f"{value} {from_u.upper()} = {value * rate:.2f} {to_u.upper()}"
        except Exception:
            pass
        return f"Wechselkurs {from_u.upper()} → {to_u.upper()} nicht verfügbar."

    return f"Unbekannte Einheiten: {from_unit} → {to_unit}"


# ── Wecker / Alarm ──────────────────────────────────────────────────────────

@_tool("set_alarm")
def set_alarm(time_str: str, label: str = "Alarm") -> str:
    """Set an alarm for a specific time (HH:MM format). Uses winsound to alert."""
    try:
        target_time = datetime.datetime.strptime(time_str.strip(), "%H:%M").time()
        now = datetime.datetime.now()
        target = datetime.datetime.combine(now.date(), target_time)
        if target <= now:
            target += datetime.timedelta(days=1)
        delay = (target - now).total_seconds()

        def _fire():
            time.sleep(delay)
            label_safe = label.replace("'", "''")
            # Beep sequence + notification
            run_command(
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.MessageBox]::Show('JARVIS ALARM: {label_safe}', 'JARVIS', 'OK', 'Exclamation')",
                timeout=5,
            )
            for _ in range(5):
                try:
                    import winsound
                    winsound.Beep(1000, 300)
                    time.sleep(0.2)
                except Exception:
                    break

        threading.Thread(target=_fire, daemon=True).start()
        return f"Alarm '{label}' gestellt für {time_str} (in {int(delay//3600)}h {int((delay%3600)//60)}min)."
    except ValueError:
        return f"Ungültiges Zeitformat: {time_str}. Nutze HH:MM (z.B. 07:30)."


# ── Spotify (wenn installiert) ───────────────────────────────────────────────

@_tool("play_music")
def play_music(query: str) -> str:
    """Search and play music. Opens Spotify/YouTube with the search query."""
    # Versuche Spotify zuerst
    result = run_command("Get-Process Spotify -ErrorAction SilentlyContinue", timeout=5)
    if result and "Spotify" in result:
        # Spotify läuft — Suche über URI
        q = urllib.parse.quote(query)
        import webbrowser
        webbrowser.open(f"spotify:search:{q}")
        return f"Spotify-Suche gestartet: {query}"
    # Fallback: YouTube Music
    import webbrowser
    q = urllib.parse.quote(query)
    webbrowser.open(f"https://music.youtube.com/search?q={q}")
    return f"YouTube Music Suche: {query}"


@_tool("get_location")
def get_location() -> str:
    """Get the approximate current location based on IP address."""
    try:
        r = requests.get("https://ipapi.co/json/", timeout=8,
                         headers={"User-Agent": "JARVIS-Assistant/2.0"})
        d = r.json()
        return (
            f"Standort: {d.get('city', '?')}, {d.get('region', '?')}, {d.get('country_name', '?')}. "
            f"Koordinaten: {d.get('latitude', '?')}, {d.get('longitude', '?')}. "
            f"ISP: {d.get('org', '?')}. Zeitzone: {d.get('timezone', '?')}."
        )
    except Exception as e:
        return f"Standort nicht ermittelbar: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  SELBSTVERBESSERUNG — JARVIS kann sich selbst weiterentwickeln
# ══════════════════════════════════════════════════════════════════════════════

_JARVIS_ROOT = Path(__file__).parent
_LEARNINGS_FILE = _JARVIS_ROOT / "data" / "learnings.json"
_BACKUP_DIR = _JARVIS_ROOT / "backups"

_ALLOWED_FILES = {"tools.py", "Main.py", "hologram.py", "hologram.html"}


def _load_learnings() -> list:
    """Lade alle bisherigen Erkenntnisse."""
    if _LEARNINGS_FILE.exists():
        try:
            return json.loads(_LEARNINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_learnings(lessons: list):
    """Speichere Erkenntnisse persistent."""
    _LEARNINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LEARNINGS_FILE.write_text(
        json.dumps(lessons, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _backup_file(filename: str):
    """Erstellt ein Backup einer Datei bevor sie editiert wird."""
    src = _JARVIS_ROOT / filename
    if not src.exists():
        return
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = _BACKUP_DIR / f"{filename}.{ts}.bak"
    dst.write_bytes(src.read_bytes())


def _validate_python(filepath: Path) -> tuple:
    """Prüft ob eine Python-Datei syntaktisch korrekt ist. Returns (ok, error)."""
    import ast
    try:
        ast.parse(filepath.read_text(encoding="utf-8"))
        return True, ""
    except SyntaxError as e:
        return False, f"Zeile {e.lineno}: {e.msg}"


def get_learnings_for_prompt() -> str:
    """Gibt alle Erkenntnisse als Text zurück (wird in System-Prompt geladen)."""
    lessons = _load_learnings()
    if not lessons:
        return ""
    lines = ["GELERNTE ERKENNTNISSE (aus früheren Sitzungen):"]
    for i, l in enumerate(lessons[-30:], 1):  # letzte 30
        cat = l.get("category", "")
        txt = l.get("lesson", "")
        lines.append(f"  {i}. [{cat}] {txt}")
    return "\n".join(lines)


@_tool("add_new_tool")
def add_new_tool(name: str, description: str, parameters: str,
                 required: str = "", code: str = "") -> str:
    """Erstellt ein neues Tool und fügt es permanent zu tools.py hinzu."""
    # Validierung
    if not name.isidentifier():
        return f"Ungültiger Tool-Name: {name}"
    if name in _TOOL_MAP:
        return f"Tool '{name}' existiert bereits."

    # Parameter parsen
    try:
        params = json.loads(parameters) if parameters else {}
    except json.JSONDecodeError as e:
        return f"Ungültiges Parameter-JSON: {e}"

    req_list = [r.strip() for r in required.split(",") if r.strip()] if required else []

    # Backup erstellen
    _backup_file("tools.py")

    # Tool-Definition bauen
    tool_def = {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": params,
            "required": req_list,
        },
    }

    # An tools.py anhängen
    tools_path = _JARVIS_ROOT / "tools.py"
    content = tools_path.read_text(encoding="utf-8")

    # Implementation anhängen
    impl_code = f'''

@_tool("{name}")
{code}
'''
    content += impl_code
    tools_path.write_text(content, encoding="utf-8")

    # Syntax prüfen
    ok, err = _validate_python(tools_path)
    if not ok:
        # Rollback
        backup = sorted(_BACKUP_DIR.glob(f"tools.py.*.bak"))[-1]
        tools_path.write_bytes(backup.read_bytes())
        return f"SYNTAX-FEHLER — Rollback durchgeführt: {err}"

    # Definition zur Laufzeit registrieren
    TOOL_DEFINITIONS.append(tool_def)

    # Funktion zur Laufzeit laden
    try:
        exec(compile(impl_code, "new_tool", "exec"), globals())
    except Exception as e:
        return f"Tool geschrieben, aber Laufzeit-Fehler: {e}. Funktioniert nach Neustart."

    return (
        f"Neues Tool '{name}' erstellt und registriert.\n"
        f"Definition + Implementation in tools.py gespeichert.\n"
        f"Sofort verfügbar (kein Neustart nötig)."
    )


@_tool("edit_own_code")
def edit_own_code(file: str, find: str, replace: str) -> str:
    """Editiert JARVIS-Quellcode mit Find & Replace. Backup + Syntax-Check."""
    if file not in _ALLOWED_FILES:
        return f"Datei '{file}' darf nicht editiert werden. Erlaubt: {_ALLOWED_FILES}"

    filepath = _JARVIS_ROOT / file
    if not filepath.exists():
        return f"Datei nicht gefunden: {file}"

    content = filepath.read_text(encoding="utf-8")
    count = content.count(find)

    if count == 0:
        return f"Text nicht gefunden in {file}. Prüfe den 'find'-Parameter."
    if count > 1:
        return f"Text {count}× gefunden — muss einzigartig sein. Erweitere den Suchtext."

    # Backup erstellen
    _backup_file(file)

    # Ersetzen
    new_content = content.replace(find, replace, 1)
    filepath.write_text(new_content, encoding="utf-8")

    # Python-Dateien auf Syntax prüfen
    if file.endswith(".py"):
        ok, err = _validate_python(filepath)
        if not ok:
            # Rollback
            filepath.write_text(content, encoding="utf-8")
            return f"SYNTAX-FEHLER — Rollback: {err}. Originalcode wiederhergestellt."

    diff_lines = len(replace.splitlines()) - len(find.splitlines())
    return (
        f"{file} erfolgreich editiert.\n"
        f"Geändert: {len(find)} → {len(replace)} Zeichen "
        f"({'+' if diff_lines >= 0 else ''}{diff_lines} Zeilen).\n"
        f"Backup gespeichert in backups/."
    )


@_tool("read_own_code")
def read_own_code(file: str, start: int = 1, end: int = 100) -> str:
    """Liest JARVIS-Quellcode zur Analyse."""
    if file not in _ALLOWED_FILES:
        return f"Datei '{file}' nicht erlaubt. Erlaubt: {_ALLOWED_FILES}"

    filepath = _JARVIS_ROOT / file
    if not filepath.exists():
        return f"Datei nicht gefunden: {file}"

    lines = filepath.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = max(1, start)
    end = min(end, total)

    selected = lines[start - 1:end]
    numbered = [f"{i:4d}  {line}" for i, line in enumerate(selected, start)]
    return f"── {file} (Zeilen {start}-{end} von {total}) ──\n" + "\n".join(numbered)


@_tool("learn")
def learn(lesson: str, category: str = "behavior") -> str:
    """Speichert eine Erkenntnis permanent."""
    valid_cats = {"user_pref", "bug_fix", "optimization", "new_capability", "behavior"}
    if category not in valid_cats:
        category = "behavior"

    lessons = _load_learnings()

    # Duplikat-Check
    for existing in lessons:
        if existing.get("lesson", "").lower() == lesson.lower():
            return "Diese Erkenntnis ist bereits gespeichert."

    lessons.append({
        "lesson": lesson,
        "category": category,
        "timestamp": datetime.datetime.now().isoformat(),
    })
    _save_learnings(lessons)

    return f"Erkenntnis gespeichert [{category}]: {lesson}"


@_tool("self_reflect")
def self_reflect() -> str:
    """Liest eigenen Code + Learnings und erstellt einen Verbesserungsplan."""
    # Statistiken sammeln
    tools_path = _JARVIS_ROOT / "tools.py"
    main_path = _JARVIS_ROOT / "Main.py"

    tools_lines = len(tools_path.read_text(encoding="utf-8").splitlines()) if tools_path.exists() else 0
    main_lines = len(main_path.read_text(encoding="utf-8").splitlines()) if main_path.exists() else 0

    tool_count = len(TOOL_DEFINITIONS)
    lessons = _load_learnings()
    lesson_count = len(lessons)

    # Backup-Verlauf
    backup_count = len(list(_BACKUP_DIR.glob("*.bak"))) if _BACKUP_DIR.exists() else 0

    # Letzte Learnings
    recent = lessons[-5:] if lessons else []
    recent_text = "\n".join(
        f"  - [{l.get('category','')}] {l.get('lesson','')}" for l in recent
    ) if recent else "  (keine)"

    return (
        f"=== JARVIS Selbstanalyse ===\n"
        f"Codebasis: tools.py ({tools_lines} Zeilen), Main.py ({main_lines} Zeilen)\n"
        f"Tools: {tool_count} registriert\n"
        f"Erkenntnisse: {lesson_count} gespeichert\n"
        f"Backups: {backup_count} Dateien\n"
        f"\nLetzte Erkenntnisse:\n{recent_text}\n"
        f"\nVERBESSERUNGSMÖGLICHKEITEN:\n"
        f"- Neue Tools: Überlege welche Fähigkeiten fehlen (add_new_tool)\n"
        f"- Code-Qualität: Lies eigenen Code (read_own_code) und optimiere (edit_own_code)\n"
        f"- Verhalten: Nutze learn() für Nutzer-Präferenzen und Fehler\n"
        f"- Hologramm: Überprüfe hologram.html auf Verbesserungen\n"
        f"\nNutze add_new_tool, edit_own_code, oder learn um dich zu verbessern."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  IRON MAN JARVIS — Erweiterte Fähigkeiten
# ══════════════════════════════════════════════════════════════════════════════

_CONTACTS_FILE = _JARVIS_ROOT / "data" / "contacts.json"


@_tool("smart_home")
def smart_home(action: str, target: str = "all") -> str:
    """Control smart home devices via PowerShell/REST."""
    action = action.lower().strip()

    # Philips Hue Bridge discovery
    if action == "status":
        ps = (
            "try { $hue = (Invoke-RestMethod 'https://discovery.meethue.com/' -TimeoutSec 5); "
            "\"Hue Bridge: $($hue[0].internalipaddress)\" } catch { 'Kein Hue Bridge gefunden.' }; "
            "try { $mqtt = Get-Process mosquitto -ErrorAction SilentlyContinue; "
            "if($mqtt){'MQTT Broker: aktiv'}else{'MQTT: nicht aktiv'} } catch {}"
        )
        return run_command(ps, timeout=10)

    # Generic via PowerShell — user can configure endpoints
    config_path = _JARVIS_ROOT / "data" / "smart_home.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            endpoint = config.get("endpoint", "")
            api_key = config.get("api_key", "")
            if endpoint:
                ps = f"Invoke-RestMethod -Uri '{endpoint}/{action}' -Headers @{{'Authorization'='{api_key}'}} -TimeoutSec 5"
                return run_command(ps, timeout=10)
        except Exception:
            pass

    return (
        f"Smart-Home-Aktion '{action}' registriert. "
        f"Konfiguriere data/smart_home.json mit deinem Smart-Home-Endpoint. "
        f"Format: {{\"endpoint\": \"http://hue-bridge/api\", \"api_key\": \"...\"}}"
    )


@_tool("security_scan")
def security_scan(scope: str = "quick") -> str:
    """Full security assessment of the PC."""
    scope = scope.lower().strip()

    if scope == "network":
        ps = (
            "Write-Output '=== Netzwerk-Scan ==='; "
            "Get-NetTCPConnection -State Established | "
            "Select-Object -First 15 LocalPort,RemoteAddress,RemotePort,OwningProcess | "
            "Format-Table -AutoSize | Out-String; "
            "Write-Output ''; "
            "arp -a | Out-String"
        )
        return run_command(ps, timeout=20)

    checks = [
        # Firewall
        "Write-Output '=== Firewall ==='; "
        "(Get-NetFirewallProfile | Select-Object Name,Enabled | Format-Table | Out-String);",
        # Defender
        "Write-Output '=== Windows Defender ==='; "
        "try{$d=Get-MpComputerStatus; \"Status: $($d.AMRunningMode) | Definitionen: $($d.AntivirusSignatureLastUpdated)\"}catch{'Nicht verfügbar'};",
        # Offene Ports
        "Write-Output '=== Offene Ports ==='; "
        "(Get-NetTCPConnection -State Listen | Select-Object -First 10 LocalPort,OwningProcess | Format-Table | Out-String);",
        # Login-Versuche
        "Write-Output '=== Letzte Logins ==='; "
        "try{Get-WinEvent -LogName Security -FilterXPath '*[System[EventID=4624]]' -MaxEvents 5 | "
        "Select-Object TimeCreated,Message | Format-List | Out-String}catch{'Kein Zugriff auf Security Log.'};",
    ]

    if scope == "full":
        checks.append(
            "Write-Output '=== Verdächtige Prozesse ==='; "
            "Get-Process | Where-Object {$_.Path -and $_.Path -notlike '*Windows*' -and $_.Path -notlike '*Program Files*'} | "
            "Select-Object -First 10 Name,Id,Path | Format-Table -AutoSize | Out-String;"
        )

    return run_command(" ".join(checks), timeout=30)


@_tool("get_route")
def get_route(origin: str, destination: str, mode: str = "driving") -> str:
    """Get directions and travel time using OSRM (free, no API key)."""
    try:
        # Geocode origin
        r1 = requests.get(
            f"https://nominatim.openstreetmap.org/search",
            params={"q": origin, "format": "json", "limit": 1},
            timeout=8, headers={"User-Agent": "JARVIS-Assistant/2.0"},
        )
        r2 = requests.get(
            f"https://nominatim.openstreetmap.org/search",
            params={"q": destination, "format": "json", "limit": 1},
            timeout=8, headers={"User-Agent": "JARVIS-Assistant/2.0"},
        )
        loc1 = r1.json()[0]
        loc2 = r2.json()[0]

        # OSRM routing
        profile = "car" if mode == "driving" else "foot"
        coords = f"{loc1['lon']},{loc1['lat']};{loc2['lon']},{loc2['lat']}"
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/{profile}/{coords}",
            params={"overview": "false"},
            timeout=10,
        )
        route = r.json()["routes"][0]
        dist_km = route["distance"] / 1000
        dur_min = route["duration"] / 60

        return (
            f"Route: {loc1['display_name']} → {loc2['display_name']}\n"
            f"Entfernung: {dist_km:.1f} km\n"
            f"Fahrzeit: {int(dur_min)} Minuten ({mode})"
        )
    except Exception as e:
        return f"Route nicht ermittelbar: {e}"


@_tool("get_stock")
def get_stock(symbol: str) -> str:
    """Get stock/crypto price from free APIs."""
    symbol = symbol.strip().upper()
    # Crypto check
    crypto_map = {"BTC": "bitcoin", "ETH": "ethereum", "BITCOIN": "bitcoin",
                  "ETHEREUM": "ethereum", "DOGE": "dogecoin", "SOL": "solana"}
    crypto_id = crypto_map.get(symbol, symbol.lower())

    # Try CoinGecko for crypto
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price",
            params={"ids": crypto_id, "vs_currencies": "usd,eur", "include_24hr_change": "true"},
            timeout=8,
        )
        d = r.json().get(crypto_id)
        if d:
            return (
                f"{crypto_id.title()}: ${d['usd']:,.2f} / {d['eur']:,.2f} EUR "
                f"(24h: {d.get('usd_24h_change', 0):+.2f}%)"
            )
    except Exception:
        pass

    # Yahoo Finance scrape for stocks
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "2d"},
            timeout=8, headers={"User-Agent": "JARVIS-Assistant/2.0"},
        )
        d = r.json()["chart"]["result"][0]
        meta = d["meta"]
        price = meta["regularMarketPrice"]
        prev = meta["chartPreviousClose"]
        change = ((price - prev) / prev) * 100
        currency = meta.get("currency", "USD")
        return f"{symbol}: {price:.2f} {currency} ({change:+.2f}% heute)"
    except Exception as e:
        return f"Kurs nicht verfügbar für '{symbol}': {e}"


@_tool("research")
def research(topic: str, depth: str = "standard") -> str:
    """Deep research: search multiple sources, compile a briefing."""
    max_results = {"brief": 3, "standard": 5, "deep": 10}.get(depth, 5)
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(topic, max_results=max_results))
    except Exception:
        hits = []

    if not hits:
        return f"Keine Ergebnisse für Recherche: {topic}"

    report = [f"=== JARVIS Recherche: {topic} ===\n"]
    for i, h in enumerate(hits, 1):
        report.append(
            f"[{i}] {h.get('title', 'Unbekannt')}\n"
            f"    {h.get('body', '')[:300]}\n"
            f"    Quelle: {h.get('href', '')}\n"
        )
    report.append(f"\n{len(hits)} Quellen analysiert. Tiefe: {depth}.")
    return "\n".join(report)


@_tool("system_diagnostics")
def system_diagnostics() -> str:
    """Full system diagnostic — hardware health, temps, errors."""
    checks = (
        "Write-Output '=== JARVIS System-Diagnostik ==='; "
        "Write-Output ''; "
        # Uptime
        "Write-Output \"Uptime: $((Get-CimInstance Win32_OperatingSystem).LastBootUpTime)\"; "
        # CPU + RAM
        f"Write-Output \"CPU: {psutil.cpu_percent()}%  RAM: {psutil.virtual_memory().percent}%\"; "
        # Disk Health
        "Write-Output ''; Write-Output '--- Festplatten ---'; "
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,Size | Format-Table -AutoSize | Out-String; "
        # GPU
        "Write-Output '--- GPU ---'; "
        "try{(Get-WmiObject Win32_VideoController | Select-Object Name,DriverVersion,Status | Format-Table | Out-String)}catch{'N/A'}; "
        # Event Log Errors (last 5)
        "Write-Output '--- Letzte Fehler (Event Log) ---'; "
        "try{Get-WinEvent -LogName System -MaxEvents 5 -FilterXPath '*[System[Level=2]]' | "
        "Select-Object TimeCreated,Message | Format-List | Out-String}catch{'Keine Fehler.'}; "
        # Pending Updates
        "Write-Output '--- Windows Updates ---'; "
        "try{$s=New-Object -ComObject Microsoft.Update.Session; "
        "$u=$s.CreateUpdateSearcher().Search('IsInstalled=0').Updates; "
        "\"$($u.Count) ausstehende Updates\"}catch{'Update-Status nicht ermittelbar.'}; "
        # Startup programs
        "Write-Output '--- Autostart ---'; "
        "Get-CimInstance Win32_StartupCommand | Select-Object -First 10 Name,Command | Format-Table -AutoSize | Out-String"
    )
    return run_command(checks, timeout=45)


@_tool("manage_contacts")
def manage_contacts(action: str, name: str = "", phone: str = "",
                    email: str = "", notes: str = "") -> str:
    """Manage contact database."""
    contacts = {}
    if _CONTACTS_FILE.exists():
        try:
            contacts = json.loads(_CONTACTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            contacts = {}

    action = action.lower().strip()

    if action == "list":
        if not contacts:
            return "Keine Kontakte gespeichert."
        lines = ["Kontakte:"]
        for n, info in sorted(contacts.items()):
            parts = [n]
            if info.get("phone"):
                parts.append(info["phone"])
            if info.get("email"):
                parts.append(info["email"])
            lines.append("  " + " — ".join(parts))
        return "\n".join(lines)

    elif action == "find" and name:
        query = name.lower()
        found = {n: i for n, i in contacts.items() if query in n.lower()}
        if not found:
            return f"Kein Kontakt gefunden: {name}"
        lines = []
        for n, i in found.items():
            lines.append(f"{n}: Tel. {i.get('phone','?')} | Mail: {i.get('email','?')} | {i.get('notes','')}")
        return "\n".join(lines)

    elif action == "add" and name:
        contacts[name] = {"phone": phone, "email": email, "notes": notes}
        _CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONTACTS_FILE.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"Kontakt '{name}' gespeichert."

    elif action == "remove" and name:
        removed = contacts.pop(name, None)
        if not removed:
            # Fuzzy match
            for k in list(contacts.keys()):
                if name.lower() in k.lower():
                    contacts.pop(k)
                    removed = True
                    break
        if removed:
            _CONTACTS_FILE.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"Kontakt '{name}' entfernt."
        return f"Kontakt '{name}' nicht gefunden."

    return f"Unbekannte Aktion: {action}. Nutze: add, find, list, remove."


@_tool("summarize_text")
def summarize_text(text: str, length: str = "medium") -> str:
    """Summarize text — if it's a file path, read it first."""
    p = Path(text.strip()).expanduser()
    if p.exists() and p.is_file():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:10000]
        except Exception:
            pass

    # Use the LLM itself for summarization (return text for the AI to summarize)
    word_count = len(text.split())
    return (
        f"Text zum Zusammenfassen ({word_count} Wörter, Ziel: {length}):\n\n"
        f"{text[:5000]}"
    )


@_tool("generate_code")
def generate_code(language: str, task: str, execute: bool = False,
                  filename: str = "") -> str:
    """Generate and optionally execute code."""
    # Return the task description for the AI to generate the code
    result = f"Code-Generierung: {language} — {task}"

    if execute and language.lower() in ("python", "powershell", "batch"):
        if not filename:
            ext = {"python": ".py", "powershell": ".ps1", "batch": ".bat"}
            filename = f"jarvis_generated{ext.get(language.lower(), '.txt')}"
        result += f"\nDatei: {filename}\nAusführung: angefordert"
    return result


@_tool("screen_ocr")
def screen_ocr() -> str:
    """Take screenshot + OCR via PowerShell (Windows built-in)."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "$bmp=[System.Drawing.Bitmap]::new($s.Width,$s.Height); "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($s.Location,[System.Drawing.Point]::Empty,$s.Size); "
        "$tmp=\"$env:TEMP\\jarvis_ocr.png\"; $bmp.Save($tmp); "
        "$g.Dispose(); $bmp.Dispose(); "
        # OCR via Windows.Media.Ocr
        "Add-Type -AssemblyName 'Windows.Foundation','Windows.Media.Ocr' -ErrorAction SilentlyContinue; "
        "try { "
        "  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages(); "
        "  $file = [Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp).GetAwaiter().GetResult(); "
        "  $stream = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read).GetAwaiter().GetResult(); "
        "  $decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).GetAwaiter().GetResult(); "
        "  $bitmap = $decoder.GetSoftwareBitmapAsync().GetAwaiter().GetResult(); "
        "  $result = $engine.RecognizeAsync($bitmap).GetAwaiter().GetResult(); "
        "  $result.Text "
        "} catch { 'OCR nicht verfügbar. Windows 10+ mit Sprachpaket erforderlich.' }"
    )
    return run_command(ps, timeout=20)


@_tool("dictate")
def dictate(text: str) -> str:
    """Paste long text into active window via clipboard."""
    clipboard_set(text)
    time.sleep(0.1)
    type_text("^v")  # Ctrl+V
    return f"Text eingefügt ({len(text)} Zeichen)."


@_tool("manage_startup")
def manage_startup(action: str, target: str = "") -> str:
    """Manage Windows startup programs."""
    action = action.lower().strip()
    if action == "list":
        ps = "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location | Format-Table -AutoSize | Out-String"
        return run_command(ps, timeout=10)
    elif action == "add" and target:
        name = Path(target).stem
        ps = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
            f"\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{name}.lnk\"); "
            f"$s.TargetPath = '{target}'; $s.Save(); 'Autostart hinzugefügt: {name}'"
        )
        return run_command(ps, timeout=8)
    elif action == "remove" and target:
        target_e = target.replace("'", "''")
        ps = (
            f"$files = Get-ChildItem \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\" | "
            f"Where-Object {{$_.BaseName -like '*{target_e}*'}}; "
            f"if($files){{$files | Remove-Item -Force; 'Entfernt: ' + ($files.Name -join ', ')}} "
            f"else{{'Nicht gefunden: {target_e}'}}"
        )
        return run_command(ps, timeout=8)
    return f"Unbekannte Aktion: {action}. Nutze: list, add, remove."


@_tool("system_cleanup")
def system_cleanup(scope: str = "temp") -> str:
    """Clean up system: temp files, caches, logs."""
    scope = scope.lower().strip()

    if scope == "preview":
        ps = (
            "$size = 0; "
            "$tmp = Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum; "
            "$size += $tmp.Sum; "
            "$wt = Get-ChildItem 'C:\\Windows\\Temp' -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum; "
            "$size += $wt.Sum; "
            "\"Temp-Dateien: $([math]::Round(($tmp.Sum)/1MB, 1)) MB\"; "
            "\"Windows-Temp: $([math]::Round(($wt.Sum)/1MB, 1)) MB\"; "
            "\"Gesamt: $([math]::Round($size/1MB, 1)) MB freizugeben\""
        )
        return run_command(ps, timeout=15)

    checks = [
        "Remove-Item \"$env:TEMP\\*\" -Recurse -Force -ErrorAction SilentlyContinue; ",
    ]
    if scope == "full":
        checks.extend([
            "Remove-Item 'C:\\Windows\\Temp\\*' -Recurse -Force -ErrorAction SilentlyContinue; ",
            "Remove-Item \"$env:LOCALAPPDATA\\Microsoft\\Windows\\INetCache\\*\" -Recurse -Force -ErrorAction SilentlyContinue; ",
            "Remove-Item \"$env:LOCALAPPDATA\\Temp\\*\" -Recurse -Force -ErrorAction SilentlyContinue; ",
        ])
    checks.append("'System-Bereinigung abgeschlossen.'")
    return run_command(" ".join(checks), timeout=30)


    """Get the approximate current location based on IP address."""
    try:
        r = requests.get("https://ipapi.co/json/", timeout=8,
                         headers={"User-Agent": "JARVIS-Assistant/2.0"})
        d = r.json()
        return (
            f"Standort: {d.get('city', '?')}, {d.get('region', '?')}, {d.get('country_name', '?')}. "
            f"Koordinaten: {d.get('latitude', '?')}, {d.get('longitude', '?')}. "
            f"ISP: {d.get('org', '?')}. Zeitzone: {d.get('timezone', '?')}."
        )
    except Exception as e:
        return f"Standort nicht ermittelbar: {e}"
