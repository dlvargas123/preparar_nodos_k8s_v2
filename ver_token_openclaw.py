import os
import json
from pathlib import Path


def read_env_file(path: Path, key: str):
    if not path.exists():
        return None

    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        k, v = line.split("=", 1)

        if k.strip() == key:
            return v.strip().strip('"').strip("'")

    return None


def read_openclaw_json(path: Path):
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception as e:
        print(f"No pude leer {path}: {e}")
        return None

    # Busca gateway.auth.token
    token = (
        data.get("gateway", {})
            .get("auth", {})
            .get("token")
    )

    if token:
        return token

    # Busca env.OPENCLAW_GATEWAY_TOKEN
    token = data.get("env", {}).get("OPENCLAW_GATEWAY_TOKEN")

    if token:
        return token

    return None


sources = [
    ("variable de entorno", os.getenv("OPENCLAW_GATEWAY_TOKEN")),
    ("./.env", read_env_file(Path(".env"), "OPENCLAW_GATEWAY_TOKEN")),
    ("~/.openclaw/.env", read_env_file(Path.home() / ".openclaw" / ".env", "OPENCLAW_GATEWAY_TOKEN")),
    ("~/.openclaw/openclaw.json", read_openclaw_json(Path.home() / ".openclaw" / "openclaw.json")),
]

for name, token in sources:
    if token:
        print(f"Encontrado en: {name}")
        print(f"Token: {token}")
        break
else:
    print("No encontré OPENCLAW_GATEWAY_TOKEN ni gateway.auth.token.")
