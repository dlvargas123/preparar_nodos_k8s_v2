#!/usr/bin/env python3

import subprocess
import json
import sys
import shutil


# ============================================================
# CONFIGURACION
# ============================================================

LOG_LINES = 100


# ============================================================
# UTILIDADES
# ============================================================

def run_cmd(cmd, check=False):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def kubectl_json(args):
    rc, stdout, stderr = run_cmd(["kubectl"] + args)

    if rc != 0:
        print(f"[ERROR] kubectl {' '.join(args)}")
        if stderr:
            print(stderr.strip())
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        print("[ERROR] No fue posible interpretar la respuesta JSON.")
        return None


def header(text, char="="):
    print()
    print(char * 120)
    print(f" {text}")
    print(char * 120)


def subheader(text):
    print()
    print("-" * 120)
    print(f" {text}")
    print("-" * 120)


# ============================================================
# DEPLOYMENTS NO READY
# ============================================================

def get_not_ready_deployments():

    data = kubectl_json([
        "get",
        "deployments",
        "-A",
        "-o",
        "json"
    ])

    if data is None:
        sys.exit(1)

    deployments = []

    for deploy in data.get("items", []):

        metadata = deploy.get("metadata", {})
        spec = deploy.get("spec", {})
        status = deploy.get("status", {})

        namespace = metadata.get("namespace", "")
        name = metadata.get("name", "")

        desired = spec.get("replicas", 1)
        if desired is None:
            desired = 0

        ready = status.get("readyReplicas", 0) or 0
        available = status.get("availableReplicas", 0) or 0
        updated = status.get("updatedReplicas", 0) or 0
        unavailable = status.get("unavailableReplicas", 0) or 0

        # Deployment considerado sano cuando Ready y Available
        # coinciden con las replicas deseadas.
        if ready != desired or available != desired:

            deployments.append({
                "namespace": namespace,
                "name": name,
                "desired": desired,
                "ready": ready,
                "available": available,
                "updated": updated,
                "unavailable": unavailable,
                "object": deploy
            })

    return sorted(
        deployments,
        key=lambda x: (x["namespace"], x["name"])
    )


# ============================================================
# SELECTOR DEL DEPLOYMENT
# ============================================================

def select_deployment(deployments):

    header("DEPLOYMENTS NO READY")

    if not deployments:
        print()
        print("[OK] Todos los Deployments están Ready.")
        sys.exit(0)

    print()
    print(
        f"{'#':<5}"
        f"{'NAMESPACE':<30}"
        f"{'DEPLOYMENT':<50}"
        f"{'READY':<10}"
        f"{'AVAILABLE':<12}"
        f"{'UPDATED'}"
    )

    print("-" * 120)

    for i, d in enumerate(deployments, start=1):
        print(
            f"{i:<5}"
            f"{d['namespace']:<30}"
            f"{d['name']:<50}"
            f"{d['ready']}/{d['desired']:<8}"
            f"{d['available']}/{d['desired']:<10}"
            f"{d['updated']}/{d['desired']}"
        )

    print()
    print(f"TOTAL DEPLOYMENTS NO READY: {len(deployments)}")

    while True:

        print()
        option = input(
            "Seleccione el número del Deployment a diagnosticar "
            "(q para salir): "
        ).strip()

        if option.lower() in ("q", "quit", "exit", "salir"):
            sys.exit(0)

        try:
            index = int(option)

            if 1 <= index <= len(deployments):
                return deployments[index - 1]

        except ValueError:
            pass

        print("[ERROR] Selección inválida.")


# ============================================================
# CONDICIONES DEL DEPLOYMENT
# ============================================================

def show_deployment_conditions(deployment):

    namespace = deployment["namespace"]
    name = deployment["name"]

    data = kubectl_json([
        "-n", namespace,
        "get",
        "deployment",
        name,
        "-o",
        "json"
    ])

    subheader("CONDICIONES DEL DEPLOYMENT")

    if not data:
        print("No fue posible obtener información.")
        return

    conditions = data.get("status", {}).get("conditions", [])

    if not conditions:
        print("No existen condiciones registradas.")
        return

    for condition in conditions:

        ctype = condition.get("type", "-")
        status = condition.get("status", "-")
        reason = condition.get("reason", "-")
        message = condition.get("message", "-")

        print()
        print(f"TYPE    : {ctype}")
        print(f"STATUS  : {status}")
        print(f"REASON  : {reason}")
        print(f"MESSAGE : {message}")


# ============================================================
# OBTENER SELECTOR DEL DEPLOYMENT
# ============================================================

def get_deployment_selector(namespace, name):

    data = kubectl_json([
        "-n", namespace,
        "get",
        "deployment",
        name,
        "-o",
        "json"
    ])

    if not data:
        return None

    labels = (
        data
        .get("spec", {})
        .get("selector", {})
        .get("matchLabels", {})
    )

    if not labels:
        return None

    return ",".join(
        f"{key}={value}"
        for key, value in labels.items()
    )


# ============================================================
# OBTENER PODS
# ============================================================

def get_pods(namespace, deployment_name):

    selector = get_deployment_selector(
        namespace,
        deployment_name
    )

    if selector:

        data = kubectl_json([
            "-n", namespace,
            "get",
            "pods",
            "-l", selector,
            "-o",
            "json"
        ])

        if data:
            return data.get("items", [])

    # --------------------------------------------------------
    # Fallback por ReplicaSet
    # --------------------------------------------------------

    rs_data = kubectl_json([
        "-n", namespace,
        "get",
        "rs",
        "-o",
        "json"
    ])

    if not rs_data:
        return []

    replica_sets = []

    for rs in rs_data.get("items", []):

        owners = rs.get("metadata", {}).get(
            "ownerReferences", []
        )

        for owner in owners:

            if (
                owner.get("kind") == "Deployment"
                and owner.get("name") == deployment_name
            ):
                replica_sets.append(
                    rs["metadata"]["name"]
                )

    pods_data = kubectl_json([
        "-n", namespace,
        "get",
        "pods",
        "-o",
        "json"
    ])

    if not pods_data:
        return []

    pods = []

    for pod in pods_data.get("items", []):

        owners = pod.get("metadata", {}).get(
            "ownerReferences", []
        )

        for owner in owners:

            if (
                owner.get("kind") == "ReplicaSet"
                and owner.get("name") in replica_sets
            ):
                pods.append(pod)
                break

    return pods


# ============================================================
# ESTADO DE PODS
# ============================================================

def container_problem(status):

    state = status.get("state", {})

    if "waiting" in state:

        waiting = state["waiting"]

        return {
            "state": "WAITING",
            "reason": waiting.get("reason", "-"),
            "message": waiting.get("message", "-")
        }

    if "terminated" in state:

        terminated = state["terminated"]

        return {
            "state": "TERMINATED",
            "reason": terminated.get("reason", "-"),
            "message": terminated.get("message", "-"),
            "exitCode": terminated.get("exitCode"),
            "signal": terminated.get("signal")
        }

    if "running" in state:

        return {
            "state": "RUNNING",
            "reason": "-",
            "message": "-"
        }

    return {
        "state": "UNKNOWN",
        "reason": "-",
        "message": "-"
    }


def show_pod_summary(pods):

    subheader("PODS ASOCIADOS")

    if not pods:
        print()
        print("[ALERTA] No se encontraron Pods asociados al Deployment.")
        print()
        print("Esto puede indicar:")
        print(" - ReplicaSet no está creando Pods")
        print(" - problema de admission/webhook")
        print(" - selector incorrecto")
        print(" - Deployment recién creado")
        return

    print()
    print(
        f"{'#':<4}"
        f"{'POD':<60}"
        f"{'PHASE':<14}"
        f"{'READY':<10}"
        f"{'RESTARTS':<10}"
        f"{'NODE'}"
    )

    print("-" * 120)

    for i, pod in enumerate(pods, start=1):

        metadata = pod.get("metadata", {})
        status = pod.get("status", {})

        name = metadata.get("name", "-")
        phase = status.get("phase", "-")
        node = pod.get("spec", {}).get(
            "nodeName",
            "<sin-asignar>"
        )

        container_statuses = (
            status.get("containerStatuses", []) or []
        )

        total = len(container_statuses)

        ready = sum(
            1
            for c in container_statuses
            if c.get("ready") is True
        )

        restarts = sum(
            c.get("restartCount", 0)
            for c in container_statuses
        )

        print(
            f"{i:<4}"
            f"{name:<60}"
            f"{phase:<14}"
            f"{ready}/{total:<8}"
            f"{restarts:<10}"
            f"{node}"
        )


# ============================================================
# ANALISIS DEL POD
# ============================================================

def show_pod_problems(pod):

    name = pod.get("metadata", {}).get("name", "-")
    status = pod.get("status", {})

    subheader(f"ANALISIS DEL POD: {name}")

    print()
    print(f"PHASE   : {status.get('phase', '-')}")
    print(
        f"REASON  : {status.get('reason', '-')}"
    )
    print(
        f"MESSAGE : {status.get('message', '-')}"
    )

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    print()
    print("POD CONDITIONS:")

    conditions = status.get("conditions", [])

    for condition in conditions:

        if condition.get("status") != "True":

            print()
            print(
                f"  Type    : "
                f"{condition.get('type', '-')}"
            )
            print(
                f"  Status  : "
                f"{condition.get('status', '-')}"
            )
            print(
                f"  Reason  : "
                f"{condition.get('reason', '-')}"
            )
            print(
                f"  Message : "
                f"{condition.get('message', '-')}"
            )

    # --------------------------------------------------------
    # Init containers
    # --------------------------------------------------------

    init_statuses = (
        status.get("initContainerStatuses", []) or []
    )

    if init_statuses:

        print()
        print("INIT CONTAINERS:")

        for cs in init_statuses:

            info = container_problem(cs)

            print()
            print(
                f"  Container : "
                f"{cs.get('name', '-')}"
            )
            print(
                f"  Ready     : "
                f"{cs.get('ready', False)}"
            )
            print(
                f"  Restarts  : "
                f"{cs.get('restartCount', 0)}"
            )
            print(
                f"  State     : "
                f"{info.get('state')}"
            )
            print(
                f"  Reason    : "
                f"{info.get('reason')}"
            )
            print(
                f"  Message   : "
                f"{info.get('message')}"
            )

    # --------------------------------------------------------
    # Containers
    # --------------------------------------------------------

    container_statuses = (
        status.get("containerStatuses", []) or []
    )

    print()
    print("CONTAINERS:")

    if not container_statuses:
        print("  No existen containerStatuses todavía.")

    for cs in container_statuses:

        info = container_problem(cs)

        print()
        print(
            f"  Container : "
            f"{cs.get('name', '-')}"
        )
        print(
            f"  Ready     : "
            f"{cs.get('ready', False)}"
        )
        print(
            f"  Restarts  : "
            f"{cs.get('restartCount', 0)}"
        )
        print(
            f"  State     : "
            f"{info.get('state')}"
        )
        print(
            f"  Reason    : "
            f"{info.get('reason')}"
        )
        print(
            f"  Message   : "
            f"{info.get('message')}"
        )

        if info.get("exitCode") is not None:
            print(
                f"  Exit Code : "
                f"{info.get('exitCode')}"
            )

        # Previous termination
        last_state = cs.get("lastState", {})

        if "terminated" in last_state:

            terminated = last_state["terminated"]

            print("  --- Ultima terminacion ---")
            print(
                f"  Reason    : "
                f"{terminated.get('reason', '-')}"
            )
            print(
                f"  Exit Code : "
                f"{terminated.get('exitCode', '-')}"
            )
            print(
                f"  Message   : "
                f"{terminated.get('message', '-')}"
            )


# ============================================================
# EVENTS
# ============================================================

def show_events(namespace, pod_name):

    subheader(f"EVENTOS DEL POD: {pod_name}")

    rc, stdout, stderr = run_cmd([
        "kubectl",
        "-n", namespace,
        "get",
        "events",
        "--field-selector",
        f"involvedObject.name={pod_name}",
        "--sort-by=.lastTimestamp",
        "-o",
        "wide"
    ])

    if rc == 0 and stdout.strip():
        print(stdout.rstrip())
    else:
        print("No se encontraron eventos.")


# ============================================================
# DESCRIBE - SOLO SECCION EVENTS
# ============================================================

def show_describe_events(namespace, pod_name):

    subheader("MENSAJES DETALLADOS / DESCRIBE")

    rc, stdout, stderr = run_cmd([
        "kubectl",
        "-n", namespace,
        "describe",
        "pod",
        pod_name
    ])

    if rc != 0:
        print(stderr.strip())
        return

    lines = stdout.splitlines()

    capture = False

    for line in lines:

        if line.startswith("Conditions:"):
            capture = True

        if capture:
            print(line)

    if not capture:
        print(stdout)


# ============================================================
# LOGS
# ============================================================

def show_container_logs(namespace, pod):

    pod_name = pod.get(
        "metadata", {}
    ).get("name")

    spec = pod.get("spec", {})

    containers = spec.get("containers", [])

    if not containers:
        subheader("LOGS")
        print("El Pod todavía no tiene containers disponibles.")
        return

    for container in containers:

        container_name = container.get("name")

        subheader(
            f"LOG ACTUAL | POD={pod_name} | "
            f"CONTAINER={container_name}"
        )

        rc, stdout, stderr = run_cmd([
            "kubectl",
            "-n", namespace,
            "logs",
            pod_name,
            "-c", container_name,
            "--tail",
            str(LOG_LINES),
            "--timestamps"
        ])

        if stdout.strip():
            print(stdout.rstrip())

        if stderr.strip():
            print(stderr.rstrip())

        # ----------------------------------------------------
        # Determinar si hubo restart
        # ----------------------------------------------------

        statuses = (
            pod
            .get("status", {})
            .get("containerStatuses", []) or []
        )

        container_status = next(
            (
                s for s in statuses
                if s.get("name") == container_name
            ),
            None
        )

        if (
            container_status
            and container_status.get(
                "restartCount", 0
            ) > 0
        ):

            subheader(
                f"LOG ANTERIOR (--previous) | "
                f"POD={pod_name} | "
                f"CONTAINER={container_name}"
            )

            rc, stdout, stderr = run_cmd([
                "kubectl",
                "-n", namespace,
                "logs",
                pod_name,
                "-c", container_name,
                "--previous",
                "--tail",
                str(LOG_LINES),
                "--timestamps"
            ])

            if stdout.strip():
                print(stdout.rstrip())
            elif stderr.strip():
                print(stderr.rstrip())
            else:
                print(
                    "No hay log anterior disponible."
                )


# ============================================================
# DIAGNOSTICO REPLICASET
# ============================================================

def show_replicasets(namespace, deployment_name):

    subheader("REPLICASETS DEL DEPLOYMENT")

    data = kubectl_json([
        "-n", namespace,
        "get",
        "rs",
        "-o",
        "json"
    ])

    if not data:
        print("No se pudieron consultar ReplicaSets.")
        return

    found = False

    for rs in data.get("items", []):

        owners = (
            rs.get("metadata", {})
            .get("ownerReferences", [])
        )

        belongs = any(
            owner.get("kind") == "Deployment"
            and owner.get("name") == deployment_name
            for owner in owners
        )

        if not belongs:
            continue

        found = True

        metadata = rs.get("metadata", {})
        spec = rs.get("spec", {})
        status = rs.get("status", {})

        print()
        print(
            f"ReplicaSet : "
            f"{metadata.get('name', '-')}"
        )
        print(
            f"Desired    : "
            f"{spec.get('replicas', 0)}"
        )
        print(
            f"Current    : "
            f"{status.get('replicas', 0)}"
        )
        print(
            f"Ready      : "
            f"{status.get('readyReplicas', 0)}"
        )
        print(
            f"Available  : "
            f"{status.get('availableReplicas', 0)}"
        )

        conditions = status.get("conditions", [])

        for condition in conditions:
            print(
                f"Reason     : "
                f"{condition.get('reason', '-')}"
            )
            print(
                f"Message    : "
                f"{condition.get('message', '-')}"
            )

    if not found:
        print(
            "No se encontraron ReplicaSets asociados."
        )


# ============================================================
# MENU DE POD
# ============================================================

def select_pod(pods):

    if len(pods) == 1:
        return pods[0]

    print()
    print("Seleccione el Pod que desea analizar:")
    print()

    for i, pod in enumerate(pods, start=1):

        name = pod.get(
            "metadata", {}
        ).get("name", "-")

        phase = pod.get(
            "status", {}
        ).get("phase", "-")

        print(
            f"  {i}. {name} [{phase}]"
        )

    while True:

        print()
        option = input(
            "Número del Pod: "
        ).strip()

        try:
            index = int(option)

            if 1 <= index <= len(pods):
                return pods[index - 1]

        except ValueError:
            pass

        print("[ERROR] Selección inválida.")


# ============================================================
# MAIN
# ============================================================

def main():

    if not shutil.which("kubectl"):
        print(
            "[ERROR] kubectl no está instalado "
            "o no se encuentra en PATH."
        )
        sys.exit(1)

    deployments = get_not_ready_deployments()

    deployment = select_deployment(
        deployments
    )

    namespace = deployment["namespace"]
    name = deployment["name"]

    header(
        f"DIAGNOSTICO DEPLOYMENT | "
        f"{namespace}/{name}"
    )

    print()
    print(
        f"Desired     : "
        f"{deployment['desired']}"
    )
    print(
        f"Ready       : "
        f"{deployment['ready']}"
    )
    print(
        f"Available   : "
        f"{deployment['available']}"
    )
    print(
        f"Updated     : "
        f"{deployment['updated']}"
    )
    print(
        f"Unavailable : "
        f"{deployment['unavailable']}"
    )

    # Deployment
    show_deployment_conditions(
        deployment
    )

    # ReplicaSets
    show_replicasets(
        namespace,
        name
    )

    # Pods
    pods = get_pods(
        namespace,
        name
    )

    show_pod_summary(
        pods
    )

    if not pods:

        header(
            "DIAGNOSTICO FINAL"
        )

        print()
        print(
            "El Deployment está NO READY pero "
            "no existen Pods asociados."
        )
        print()
        print(
            "Revisar especialmente las condiciones "
            "del Deployment y ReplicaSet mostradas arriba."
        )

        return

    pod = select_pod(
        pods
    )

    pod_name = pod.get(
        "metadata", {}
    ).get("name")

    # Estado puntual
    show_pod_problems(
        pod
    )

    # Eventos Kubernetes
    show_events(
        namespace,
        pod_name
    )

    # Describe
    show_describe_events(
        namespace,
        pod_name
    )

    # Logs
    show_container_logs(
        namespace,
        pod
    )

    header("FIN DEL DIAGNOSTICO")

    print()
    print(
        f"Deployment analizado : "
        f"{namespace}/{name}"
    )
    print(
        f"Pod analizado        : "
        f"{pod_name}"
    )
    print()
    print(
        "[INFO] El script es SOLO LECTURA. "
        "No modifica recursos del cluster."
    )


if __name__ == "__main__":
    main()
