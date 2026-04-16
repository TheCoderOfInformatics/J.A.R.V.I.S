import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .config import TASKS_FILE
from .kernel import JarvisKernel


@dataclass
class ScheduledTask:
    name: str
    interval_minutes: float
    action: str
    next_run: float

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            name=data["name"],
            interval_minutes=float(data["interval_minutes"]),
            action=data["action"],
            next_run=float(data.get("next_run", time.time() + float(data["interval_minutes"]) * 60)),
        )


class PersistentScheduler:
    def __init__(self, kernel: JarvisKernel, check_interval: float = 10.0):
        self.kernel = kernel
        self.check_interval = check_interval
        self.tasks: List[ScheduledTask] = self._load_tasks()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="jarvis-scheduler")
        self._lock = threading.Lock()

    def _load_tasks(self) -> List[ScheduledTask]:
        try:
            data = json.loads(Path(TASKS_FILE).read_text(encoding="utf-8"))
            return [ScheduledTask.from_dict(item) for item in data]
        except Exception:
            return []

    def _save_tasks(self) -> None:
        Path(TASKS_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(TASKS_FILE).write_text(json.dumps([asdict(task) for task in self.tasks], ensure_ascii=False, indent=2), encoding="utf-8")

    def add_task(self, name: str, interval_minutes: float, action: str) -> None:
        with self._lock:
            self.tasks = [task for task in self.tasks if task.name != name]
            self.tasks.append(ScheduledTask(name=name.strip(), interval_minutes=interval_minutes, action=action.strip(), next_run=time.time() + interval_minutes * 60))
            self._save_tasks()

    def cancel_task(self, name: str) -> bool:
        with self._lock:
            before = len(self.tasks)
            self.tasks = [task for task in self.tasks if task.name != name]
            self._save_tasks()
            return len(self.tasks) < before

    def start(self) -> None:
        self._thread.start()

    def _run_loop(self) -> None:
        while True:
            now = time.time()
            due: List[ScheduledTask] = []
            with self._lock:
                for task in self.tasks:
                    if task.next_run <= now:
                        due.append(task)
                for task in due:
                    task.next_run = now + task.interval_minutes * 60
                if due:
                    self._save_tasks()
            for task in due:
                self._execute_task(task)
            time.sleep(self.check_interval)

    def _execute_task(self, task: ScheduledTask) -> None:
        prompt = f"Führe die geplante Aufgabe aus: {task.action}"
        result = self.kernel.process(prompt)
        print(f"[Timer] {task.name}: {result}")
