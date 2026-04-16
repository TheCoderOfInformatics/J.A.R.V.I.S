import argparse
import threading
import time

from .config import (
    PROACTIVE_CONTEXT_CHECK,
    PROACTIVE_MORNING_BRIEF,
    PROACTIVE_NIGHTLY_SUMMARY,
    PROACTIVE_SYSTEM_CHECK,
)
from .kernel import JarvisKernel
from .scheduler import PersistentScheduler
from .tools import cancel_task, list_tasks, schedule_task


def _interactive_prompt(kernel: JarvisKernel) -> None:
    print("Jarvis v2.0 gestartet. Tippe 'exit' zum Beenden.")
    while True:
        try:
            prompt = input("Du: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBeende Jarvis.")
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit", "ende"}:
            break
        response = kernel.process(prompt)
        print(f"JARVIS: {response}")


def _start_proactive_loop(kernel: JarvisKernel) -> None:
    def loop() -> None:
        morning_done = False
        nightly_done = False
        last_system = 0.0
        last_context = 0.0
        while True:
            now = time.localtime()
            current = time.time()
            if 7 <= now.tm_hour < 10 and not morning_done:
                morning_done = True
                print("[JARVIS] Morgen-Briefing startet.")
                kernel.process(PROACTIVE_MORNING_BRIEF)
            if 22 <= now.tm_hour < 24 and not nightly_done:
                nightly_done = True
                print("[JARVIS] Abend-Zusammenfassung startet.")
                kernel.process(PROACTIVE_NIGHTLY_SUMMARY)
            if now.tm_hour < 2:
                nightly_done = False
            if now.tm_hour >= 11:
                morning_done = False
            if current - last_system >= 300:
                last_system = current
                kernel.process(PROACTIVE_SYSTEM_CHECK)
            if current - last_context >= 600:
                last_context = current
                kernel.process(PROACTIVE_CONTEXT_CHECK)
            time.sleep(10)

    thread = threading.Thread(target=loop, daemon=True, name="jarvis-v2-proactive")
    thread.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis v2 – verbesserte autonome KI.")
    parser.add_argument("mode", nargs="?", default="text", choices=["text", "daemon", "task"], help="Startmodus")
    parser.add_argument("action", nargs="*", help="Zusatzparameter für task-Befehle")
    args = parser.parse_args()

    kernel = JarvisKernel()
    scheduler = PersistentScheduler(kernel)
    scheduler.start()

    if args.mode == "task":
        if not args.action:
            print("Verwende: task add <name> <minuten> <aktion> | task list | task cancel <name>")
            return
        command = args.action[0].lower()
        if command == "add" and len(args.action) >= 4:
            name = args.action[1]
            interval = float(args.action[2])
            action = " ".join(args.action[3:])
            print(schedule_task(name=name, interval_minutes=interval, action=action))
            scheduler.add_task(name, interval, action)
            return
        if command == "list":
            print(list_tasks())
            return
        if command == "cancel" and len(args.action) == 2:
            print(cancel_task(name=args.action[1]))
            scheduler.cancel_task(args.action[1])
            return
        print("Unbekannter task-Befehl.")
        return

    if args.mode == "daemon":
        _start_proactive_loop(kernel)
        _interactive_prompt(kernel)
        return

    _interactive_prompt(kernel)


if __name__ == "__main__":
    main()
