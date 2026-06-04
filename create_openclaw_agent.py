#!/usr/bin/env python3
import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


OPENCLAW_HOME = Path("/root/.openclaw")
WORKSPACE_BASE = OPENCLAW_HOME / "workspace"
AGENTS_BASE = OPENCLAW_HOME / "agents"
CONFIG_FILE = OPENCLAW_HOME / "openclaw.json"


def run_cmd(cmd, check=False):
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    if result.stderr.strip():
        print(result.stderr.strip())

    if check and result.returncode != 0:
        raise RuntimeError(f"Comando falló: {' '.join(cmd)}")

    return result


def ask(prompt, default=None, required=False):
    while True:
        if default:
            value = input(f"{prompt} [{default}]: ").strip()
            value = value or default
        else:
            value = input(f"{prompt}: ").strip()

        if required and not value:
            print("Este valor es obligatorio.")
            continue

        return value


def ask_yes_no(prompt, default="s"):
    default = default.lower()
    suffix = "[S/n]" if default == "s" else "[s/N]"

    while True:
        value = input(f"{prompt} {suffix}: ").strip().lower()

        if not value:
            value = default

        if value in ("s", "si", "sí", "y", "yes"):
            return True

        if value in ("n", "no"):
            return False

        print("Responde s o n.")


def validate_agent_id(agent_id):
    if not re.match(r"^[A-Za-z0-9_-]+$", agent_id):
        raise ValueError(
            "El ID del agente solo puede contener letras, números, guion medio y guion bajo. "
            "No uses espacios, puntos ni slash."
        )


def ensure_inside_base(path, base):
    resolved_path = path.resolve()
    resolved_base = base.resolve()

    if not str(resolved_path).startswith(str(resolved_base) + os.sep):
        raise ValueError(f"Ruta inválida fuera de {resolved_base}: {resolved_path}")


def backup_config():
    if CONFIG_FILE.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = CONFIG_FILE.with_suffix(f".json.bak-{timestamp}")
        shutil.copy2(CONFIG_FILE, backup)
        print(f"Backup creado: {backup}")


def write_file(path, content, overwrite=False):
    if path.exists() and not overwrite:
        print(f"Existe, no se sobrescribe: {path}")
        return

    path.write_text(content, encoding="utf-8")
    print(f"Archivo creado/actualizado: {path}")


def create_workspace_files(workspace, agent_id, display_name, theme, emoji, overwrite=False):
    workspace.mkdir(parents=True, exist_ok=True)

    for folder in ("notes", "manifests", "scripts", "logs"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)

    write_file(
        workspace / "AGENTS.md",
        f"""# {display_name}

Eres un asistente técnico especializado en Kubernetes, Rancher, clusters, recuperación ante desastres, networking, IP plan, troubleshooting y documentación operativa.

## Alcance principal

- Apoyar tareas relacionadas con Kubernetes.
- Revisar configuraciones de clusters.
- Ayudar con comandos kubectl, helm y rancher.
- Documentar procedimientos técnicos.
- Validar inventarios, nodos, namespaces, workloads, servicios e ingress.
- Apoyar tareas de DR y ambientes productivos/dev.

## Reglas operativas

- Antes de sugerir cambios destructivos, solicitar confirmación.
- No eliminar recursos sin validar namespace, contexto y cluster.
- Priorizar comandos de solo lectura para diagnóstico.
- Documentar cada procedimiento de forma clara.
- Usar español técnico y directo.

## Rutas de trabajo

- Notes: ./notes
- Manifests: ./manifests
- Scripts: ./scripts
- Logs: ./logs
""",
        overwrite=overwrite
    )

    write_file(
        workspace / "SOUL.md",
        f"""# Personalidad del agente

Nombre: {display_name}

Asistente técnico enfocado en operación, soporte y documentación de plataformas Kubernetes, Rancher y DR.

Debe responder de forma clara, estructurada, segura y orientada a operación.
""",
        overwrite=overwrite
    )

    write_file(
        workspace / "USER.md",
        """# Preferencias del usuario

- Responder en español.
- Usar comandos Linux cuando aplique.
- Explicar brevemente qué hace cada comando.
- Evitar cambios destructivos sin confirmación.
- Priorizar soluciones prácticas para operación de infraestructura.
""",
        overwrite=overwrite
    )

    write_file(
        workspace / "IDENTITY.md",
        f"""name: {display_name}
id: {agent_id}
theme: {theme}
emoji: {emoji}
""",
        overwrite=overwrite
    )

    write_file(
        workspace / "README.md",
        f"""# {display_name}

Workspace independiente para el agente `{agent_id}`.

## Estructura

- `AGENTS.md`: instrucciones principales del agente.
- `SOUL.md`: personalidad del agente.
- `USER.md`: preferencias del usuario.
- `IDENTITY.md`: identidad del agente.
- `notes/`: notas operativas.
- `manifests/`: manifiestos YAML.
- `scripts/`: scripts auxiliares.
- `logs/`: registros o salidas de comandos.
""",
        overwrite=overwrite
    )


def register_agent(agent_id, workspace, agent_dir):
    agent_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = AGENTS_BASE / agent_id / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    commands = [
        [
            "openclaw", "agents", "add", agent_id,
            "--workspace", str(workspace),
            "--agent-dir", str(agent_dir),
            "--non-interactive"
        ],
        [
            "openclaw", "agents", "add", agent_id,
            "--workspace", str(workspace),
            "--non-interactive"
        ],
    ]

    for cmd in commands:
        result = run_cmd(cmd)
        if result.returncode == 0:
            print("Agente registrado correctamente.")
            return True

    print("No fue posible registrar el agente automáticamente con openclaw agents add.")
    return False


def set_identity(agent_id, display_name, theme, emoji):
    cmd = [
        "openclaw", "agents", "set-identity",
        "--agent", agent_id,
        "--name", display_name,
        "--theme", theme,
        "--emoji", emoji
    ]

    result = run_cmd(cmd)

    if result.returncode == 0:
        print("Identidad configurada correctamente.")
        return True

    print("No se pudo aplicar identidad por CLI. Los archivos IDENTITY.md y SOUL.md sí fueron creados.")
    return False


def set_default_agent(agent_id):
    commands = [
        ["openclaw", "agents", "set-default", agent_id],
        ["openclaw", "agents", "default", agent_id],
        ["openclaw", "agents", "use", agent_id],
    ]

    for cmd in commands:
        result = run_cmd(cmd)
        if result.returncode == 0:
            print("Agente configurado como default.")
            return True

    print("No se pudo dejar como default por CLI. Puedes hacerlo desde la UI con el botón Set Default.")
    return False


def restart_gateway():
    commands = [
        ["openclaw", "gateway", "restart"],
    ]

    for cmd in commands:
        result = run_cmd(cmd)
        if result.returncode == 0:
            print("Gateway reiniciado correctamente.")
            return True

    print("No se pudo reiniciar el gateway por CLI.")
    print("Reinicia manualmente el servicio/contenedor de OpenClaw si la UI no actualiza.")
    return False


def validate_agent(agent_id):
    print("\nValidando agente...")

    result = run_cmd(["openclaw", "agents", "list"])

    found_in_list = agent_id in result.stdout

    found_in_config = False
    if CONFIG_FILE.exists():
        try:
            data = CONFIG_FILE.read_text(encoding="utf-8")
            found_in_config = agent_id in data
        except Exception:
            found_in_config = False

    if found_in_list or found_in_config:
        print(f"Validación OK: el agente '{agent_id}' aparece registrado.")
        return True

    print(f"Advertencia: no se encontró '{agent_id}' en la salida de validación.")
    return False


def main():
    print("=== Creador interactivo de agentes OpenClaw ===\n")

    if os.geteuid() != 0:
        print("Recomendado ejecutar como root, porque estás usando /root/.openclaw.")
        if not ask_yes_no("¿Deseas continuar de todas formas?", default="n"):
            sys.exit(1)

    if not shutil.which("openclaw"):
        print("No se encontró el binario 'openclaw' en el PATH.")
        sys.exit(1)

    agent_id = ask("ID del agente", default="ifx_k8s_assistant_cr", required=True)
    validate_agent_id(agent_id)

    display_name = ask("Nombre visible", default="IFX K8s Assistant CR", required=True)
    theme = ask("Tema", default="Kubernetes / Rancher / DR", required=True)
    emoji = ask("Emoji", default="☸️", required=True)

    workspace = WORKSPACE_BASE / agent_id
    agent_root = AGENTS_BASE / agent_id
    agent_dir = agent_root / "agent"

    ensure_inside_base(workspace, WORKSPACE_BASE)
    ensure_inside_base(agent_root, AGENTS_BASE)

    print("\nSe usará la siguiente estructura independiente:")
    print(f"Workspace : {workspace}")
    print(f"Agent dir : {agent_dir}")
    print(f"Sessions  : {agent_root / 'sessions'}")

    if workspace.exists():
        print(f"\nEl workspace ya existe: {workspace}")
        overwrite = ask_yes_no("¿Deseas sobrescribir archivos base existentes?", default="n")
    else:
        overwrite = True

    proceed = ask_yes_no("\n¿Crear/registrar este agente?", default="s")
    if not proceed:
        print("Cancelado.")
        sys.exit(0)

    backup_config()

    create_workspace_files(
        workspace=workspace,
        agent_id=agent_id,
        display_name=display_name,
        theme=theme,
        emoji=emoji,
        overwrite=overwrite
    )

    registered = register_agent(agent_id, workspace, agent_dir)

    if registered:
        set_identity(agent_id, display_name, theme, emoji)

        if ask_yes_no("¿Deseas dejar este agente como default?", default="s"):
            set_default_agent(agent_id)

        if ask_yes_no("¿Deseas reiniciar el gateway de OpenClaw para refrescar la UI?", default="s"):
            restart_gateway()

        validate_agent(agent_id)

    print("\nProceso finalizado.")
    print("Abre o recarga la UI de OpenClaw y revisa el selector de agentes.")
    print(f"Agente esperado: {agent_id}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
        sys.exit(130)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
