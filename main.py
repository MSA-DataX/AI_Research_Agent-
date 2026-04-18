import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import run

DEFAULT_TASK = (
    "Recherchiere die 5 größten KI-Startups in Berlin. Extrahiere pro Firma "
    "name, website, description, founded (falls bekannt). Validiere mit "
    "mindestens 2 Quellen, speichere als ki_startups_berlin.json und gib "
    "die strukturierte Liste als finish-result zurück."
)


def main() -> None:
    task = " ".join(sys.argv[1:]).strip() or DEFAULT_TASK
    print(f"[task] {task}\n")
    output = run(task)
    print("\n[final output]")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
