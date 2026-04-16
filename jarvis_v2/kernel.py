import json
from pathlib import Path
from typing import Dict, List

from groq import Groq

from .config import DATA_DIR, MAX_HISTORY, MAX_TOOL_ITER, MODEL, SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool


class SimpleMemory:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.facts = self._load()

    def _load(self) -> List[str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def add(self, fact: str) -> None:
        fact = fact.strip()
        if fact and fact not in self.facts:
            self.facts.append(fact)
            self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.facts, ensure_ascii=False, indent=2), encoding="utf-8")

    def as_prompt(self) -> str:
        if not self.facts:
            return ""
        return "\n".join([f"Merke: {fact}" for fact in self.facts])


class JarvisKernel:
    def __init__(self):
        self.client = Groq()
        self.memory = SimpleMemory(DATA_DIR / "knowledge.json")
        self.history: List[Dict] = []
        self._groq_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in TOOL_DEFINITIONS
        ]

    def _messages(self) -> List[Dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.memory.as_prompt():
            messages.append({"role": "system", "content": self.memory.as_prompt()})
        messages.extend(self.history)
        return messages

    def _call_api(self, use_tools: bool = True):
        return self.client.chat.completions.create(
            model=MODEL,
            messages=self._messages(),
            tools=self._groq_tools if use_tools else None,
            tool_choice="auto" if use_tools else None,
            max_tokens=1024,
        )

    def process(self, user_input: str) -> str:
        self.history.append({"role": "user", "content": user_input})
        reply = ""
        for _ in range(MAX_TOOL_ITER):
            response = self._call_api(use_tools=True)
            choice = response.choices[0]
            message = choice.message
            if choice.finish_reason != "tool_calls" or not message.tool_calls:
                reply = message.content or ""
                self.history.append({"role": "assistant", "content": reply})
                break
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
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = execute_tool(tc.function.name, args)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]
        return reply

    def save_session(self, session_id: str = "default") -> None:
        path = DATA_DIR / "sessions" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": session_id, "history": self.history}, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_session(self, session_id: str = "default") -> None:
        path = DATA_DIR / "sessions" / f"{session_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.history = data.get("history", [])
        except Exception:
            self.history = []
