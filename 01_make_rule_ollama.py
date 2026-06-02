import json
import requests
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5-coder:7b"  # ollama list で出てきた名前に変える

BASE_DIR = Path(__file__).resolve().parent
SCENARIO_TXT = BASE_DIR / "input" / "scenario.txt"
SCENARIO_TXT_FALLBACK = BASE_DIR / "input" / "scenaro.txt"
OUTPUT_JSON = BASE_DIR / "output" / "scenario_rule.json"

SYSTEM_PROMPT = """
あなたは都市計画シナリオを擬似人流データの変更ルールに変換するアシスタントです。

入力された自然言語を、以下のJSON形式に変換してください。
出力はJSONのみ。説明文、Markdown、コードブロックは禁止です。

{
  "scenario_name": "",
  "scenario_type": "",
  "target_area": "",
  "affected_purposes": [],
  "affected_time": [],
  "affected_ratio": 0.25,
  "target_location": {
    "lat": null,
    "lon": null,
    "label": ""
  },
  "movement_change": {
    "mode": "move_to_target_area",
    "strength": 0.8
  },
  "stay_time_change": {
    "enabled": true,
    "multiplier": 1.2
  },
  "visualization": {
    "before_title": "Before",
    "after_title": "After"
  },
  "questions": []
}

制約:
- affected_ratio は 0.05〜0.5 の範囲にしてください。
- strength は 0.1〜1.0 の範囲にしてください。
- 緯度経度が文から特定できない場合は null にしてください。
- 不明点がある場合でも questions は最大3つまでにしてください。
- 明日の発表用なので、デモとして違いが分かるルールを優先してください。
"""

def main():
    scenario_path = SCENARIO_TXT if SCENARIO_TXT.exists() else SCENARIO_TXT_FALLBACK
    if not scenario_path.exists():
        raise FileNotFoundError(
            f"Scenario text not found: {SCENARIO_TXT} or {SCENARIO_TXT_FALLBACK}"
        )
    scenario = scenario_path.read_text(encoding="utf-8")

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": scenario}
        ],
        "format": "json",
        "stream": False
    }

    res = requests.post(OLLAMA_URL, json=payload, timeout=120)
    res.raise_for_status()

    content = res.json()["message"]["content"]
    rule = json.loads(content)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(rule, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Saved: {OUTPUT_JSON}")
    print(json.dumps(rule, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
