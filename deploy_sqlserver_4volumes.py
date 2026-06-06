#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Despliegue de SQL Server 2022 en Kubernetes con volúmenes separados:
- data (/var/opt/mssql/data)
- log (/var/opt/mssql/log)
- tempdb (/var/opt/mssql/tempdb)
- backup (/var/opt/mssql/backup)

HADR habilitado (MSSQL_ENABLE_HADR=1), sin initContainer conflictivo.
"""

import argparse
import datetime as dt
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_NAMESPACE = "sqlserver-lab"
DEFAULT_APP = "mssql"
DEFAULT_SECRET = "mssql-secret"
DEFAULT_HEADLESS_SERVICE = "mssql-headless"
DEFAULT_SERVICE = "mssql"
DEFAULT_STORAGE_CLASS = "longhorn"
DEFAULT_STORAGE_SIZE = "10Gi"          # Tamaño para cada volumen (ajústalo)
DEFAULT_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
DEFAULT_DB = "LabKubernetesDB"
DEFAULT_CPU = "1"
DEFAULT_MEMORY = "2Gi"
DEFAULT_REPLICAS = 1
DEFAULT_TIMEOUT = 900

def run(cmd, check=True, capture=True, input_text=None):
    result = subprocess.run(cmd, input=input_text, text=True, capture_output=capture)
    if check and result.returncode != 0:
        raise RuntimeError(f"Comando falló:\n{' '.join(cmd)}\n\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return result

def ask(prompt, default=None, secret=False):
    label = prompt + (f" [{default}]" if default is not None else "") + ": "
    value = getpass.getpass(label) if secret else input(label).strip()
    return value if value else default

def ask_yes_no(prompt, default=True):
    suffix = "S/n" if default else "s/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in ("s", "si", "sí", "y", "yes")

def validate_k8s_name(value, field):
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", value or ""):
        raise ValueError(f"{field} inválido para Kubernetes: {value}")
    return value

def validate_sql_identifier(value, field):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value or ""):
        raise ValueError(f"{field} inválido para SQL Server: {value}")
    return value

def kubectl_apply_yaml(yaml_text):
    return run(["kubectl", "apply", "-f", "-"], input_text=yaml_text)

def resource_exists(namespace, kind, name):
    cmd = ["kubectl"]
    if namespace:
        cmd += ["-n", namespace]
    cmd += ["get", kind, name]
    return run(cmd, check=False).returncode == 0

def safe_delete_old_deployment(namespace, app, auto_delete=False):
    if resource_exists(namespace, "deployment", app):
        msg = f"Existe deployment/{app} en namespace {namespace}. Puede chocar con StatefulSet."
        if auto_delete:
            print(f"⚠️ {msg} Eliminando...")
            run(["kubectl", "-n", namespace, "delete", "deployment", app])
        elif ask_yes_no(f"{msg} ¿Eliminarlo?", default=False):
            run(["kubectl", "-n", namespace, "delete", "deployment", app])
        else:
            raise RuntimeError("Abortado para evitar conflicto.")

def build_manifests(cfg):
    return f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {cfg['namespace']}
---
apiVersion: v1
kind: Service
metadata:
  name: {cfg['headless_service']}
  namespace: {cfg['namespace']}
spec:
  clusterIP: None
  selector:
    app: {cfg['app']}
  ports:
    - name: tds
      port: 1433
      targetPort: 1433
---
apiVersion: v1
kind: Service
metadata:
  name: {cfg['service']}
  namespace: {cfg['namespace']}
spec:
  type: ClusterIP
  selector:
    app: {cfg['app']}
  ports:
    - name: tds
      port: 1433
      targetPort: 1433
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {cfg['app']}
  namespace: {cfg['namespace']}
spec:
  serviceName: {cfg['headless_service']}
  replicas: {cfg['replicas']}
  selector:
    matchLabels:
      app: {cfg['app']}
  template:
    metadata:
      labels:
        app: {cfg['app']}
    spec:
      securityContext:
        fsGroup: 10001
      initContainers:
        - name: fix-permissions
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              for dir in /var/opt/mssql/data /var/opt/mssql/log /var/opt/mssql/tempdb /var/opt/mssql/backup; do
                mkdir -p "$dir"
                chown -R 10001:10001 "$dir"
                chmod -R 775 "$dir"
              done
          securityContext:
            runAsUser: 0
          volumeMounts:
            - name: data
              mountPath: /var/opt/mssql/data
            - name: log
              mountPath: /var/opt/mssql/log
            - name: tempdb
              mountPath: /var/opt/mssql/tempdb
            - name: backup
              mountPath: /var/opt/mssql/backup
      containers:
        - name: mssql
          image: {cfg['image']}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 1433
          env:
            - name: ACCEPT_EULA
              value: "Y"
            - name: MSSQL_PID
              value: "Developer"
            - name: MSSQL_ENABLE_HADR
              value: "1"
            - name: HOME
              value: /var/opt/mssql
            - name: MSSQL_DATA_DIR
              value: /var/opt/mssql/data
            - name: MSSQL_LOG_DIR
              value: /var/opt/mssql/log
            - name: MSSQL_BACKUP_DIR
              value: /var/opt/mssql/backup
            - name: MSSQL_SA_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {cfg['secret']}
                  key: MSSQL_SA_PASSWORD
          resources:
            requests:
              cpu: "{cfg['cpu']}"
              memory: "{cfg['memory']}"
            limits:
              cpu: "{cfg['cpu']}"
              memory: "{cfg['memory']}"
          volumeMounts:
            - name: data
              mountPath: /var/opt/mssql/data
            - name: log
              mountPath: /var/opt/mssql/log
            - name: tempdb
              mountPath: /var/opt/mssql/tempdb
            - name: backup
              mountPath: /var/opt/mssql/backup
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ "ReadWriteOnce" ]
        storageClassName: {cfg['storage_class']}
        resources:
          requests:
            storage: {cfg['storage_size']}
    - metadata:
        name: log
      spec:
        accessModes: [ "ReadWriteOnce" ]
        storageClassName: {cfg['storage_class']}
        resources:
          requests:
            storage: {cfg['storage_size']}
    - metadata:
        name: tempdb
      spec:
        accessModes: [ "ReadWriteOnce" ]
        storageClassName: {cfg['storage_class']}
        resources:
          requests:
            storage: {cfg['storage_size']}
    - metadata:
        name: backup
      spec:
        accessModes: [ "ReadWriteOnce" ]
        storageClassName: {cfg['storage_class']}
        resources:
          requests:
            storage: {cfg['storage_size']}
"""

def create_or_update_secret(namespace, secret, password):
    print("\n🔐 Creando/actualizando Secret...")
    dry = run(["kubectl", "-n", namespace, "create", "secret", "generic", secret, f"--from-literal=MSSQL_SA_PASSWORD={password}", "--dry-run=client", "-o", "yaml"])
    kubectl_apply_yaml(dry.stdout)

def wait_for_statefulset(namespace, app, timeout):
    print(f"\n⏳ Esperando StatefulSet/{app}...")
    result = run(["kubectl", "-n", namespace, "rollout", "status", f"statefulset/{app}", f"--timeout={timeout}s"], check=False)
    if result.returncode != 0:
        print(result.stdout, result.stderr)
        show_debug(namespace, app)
        raise RuntimeError("StatefulSet no listo.")
    print(result.stdout.strip())

def wait_for_sql_ready(namespace, app, timeout):
    pod = f"{app}-0"
    print(f"\n⏳ Esperando SQL Server listo en {pod}...")
    deadline = time.time() + timeout
    last_logs = ""
    while time.time() < deadline:
        result = run(["kubectl", "-n", namespace, "logs", pod, "-c", "mssql", "--tail=120"], check=False)
        logs = (result.stdout or "") + (result.stderr or "")
        last_logs = logs[-4000:]
        if "SQL Server is now ready for client connections" in logs:
            print("✅ SQL Server listo.")
            return
        if "The system directory [/.system] could not be created" in logs:
            raise RuntimeError("Error /.system. Revisa HOME=/var/opt/mssql y permisos.")
        time.sleep(5)
    show_debug(namespace, app)
    raise RuntimeError(f"SQL Server no listo. Últimos logs:\n{last_logs}")

def get_sqlcmd_path(namespace, app):
    pod = f"{app}-0"
    for candidate in ["/opt/mssql-tools18/bin/sqlcmd", "/opt/mssql-tools/bin/sqlcmd"]:
        if run(["kubectl", "-n", namespace, "exec", pod, "-c", "mssql", "--", "test", "-x", candidate], check=False).returncode == 0:
            return candidate
    raise RuntimeError("sqlcmd no encontrado")

def exec_sql(namespace, app, password, query, database=None):
    pod = f"{app}-0"
    sqlcmd = get_sqlcmd_path(namespace, app)
    cmd = ["kubectl", "-n", namespace, "exec", pod, "-c", "mssql", "--", sqlcmd, "-S", "localhost", "-U", "sa", "-P", password, "-C", "-b"]
    if database:
        cmd += ["-d", database]
    cmd += ["-Q", query]
    return run(cmd)

def get_running_pod(namespace, app):
    pod = f"{app}-0"
    result = run(["kubectl", "-n", namespace, "get", "pod", pod, "-o", "json"])
    data = json.loads(result.stdout)
    phase = data.get("status", {}).get("phase")
    ready = any(cs.get("name") == "mssql" and cs.get("ready") for cs in data.get("status", {}).get("containerStatuses", []))
    if phase == "Running" and ready:
        return pod
    raise RuntimeError(f"{pod} no está Running/Ready.")

def create_test_db_and_record(namespace, app, password, database):
    print(f"\n🧪 Creando/validando base `{database}`...")
    exec_sql(namespace, app, password, f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE [{database}];")
    print("🧪 Creando tabla e insertando registro de prueba...")
    validation_sql = f"""
    USE [{database}];
    IF OBJECT_ID(N'dbo.prueba_k8s', N'U') IS NULL
        CREATE TABLE dbo.prueba_k8s (id INT IDENTITY PRIMARY KEY, mensaje NVARCHAR(200), fecha_utc DATETIME2 DEFAULT SYSUTCDATETIME());
    INSERT INTO dbo.prueba_k8s (mensaje) VALUES (N'Registro desde Python + StatefulSet (4 volúmenes)');
    SELECT TOP 5 id, mensaje, fecha_utc FROM dbo.prueba_k8s ORDER BY id DESC;
    """
    return exec_sql(namespace, app, password, validation_sql).stdout

def show_debug(namespace, app):
    print("\n===== DEBUG =====")
    cmds = [
        ["kubectl", "-n", namespace, "get", "pods", "-o", "wide"],
        ["kubectl", "-n", namespace, "get", "pvc"],
        ["kubectl", "-n", namespace, "describe", "pod", f"{app}-0"],
        ["kubectl", "-n", namespace, "logs", f"{app}-0", "-c", "fix-permissions", "--tail=80"],
        ["kubectl", "-n", namespace, "logs", f"{app}-0", "-c", "mssql", "--tail=100"],
        ["kubectl", "-n", namespace, "get", "events", "--sort-by=.lastTimestamp"],
    ]
    for cmd in cmds:
        print(f"\n$ {' '.join(cmd)}")
        r = run(cmd, check=False)
        print((r.stdout or "") + (r.stderr or ""))

def generate_report(cfg, validation_output, report_dir):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report = report_dir / f"reporte_sqlserver_4volumes_{now}.md"

    def safe(cmd):
        r = run(cmd, check=False)
        return ((r.stdout or "") + (r.stderr or "")).strip()

    pods = safe(["kubectl", "-n", cfg["namespace"], "get", "pods", "-o", "wide"])
    pvcs = safe(["kubectl", "-n", cfg["namespace"], "get", "pvc"])
    svcs = safe(["kubectl", "-n", cfg["namespace"], "get", "svc", "-o", "wide"])
    sts = safe(["kubectl", "-n", cfg["namespace"], "get", "statefulset", cfg["app"]])
    version = safe([
        "kubectl", "-n", cfg["namespace"],
        "exec", f"{cfg['app']}-0", "-c", "mssql", "--",
        get_sqlcmd_path(cfg["namespace"], cfg["app"]),
        "-S", "localhost", "-U", "sa", "-P", cfg["password"],
        "-C", "-Q", "SELECT @@VERSION;"
    ])

    content = (
        "# Reporte SQL Server en Kubernetes - 4 volúmenes separados\n\n"
        f"Fecha: {dt.datetime.now().isoformat(timespec='seconds')}\n\n"
        "## Estado\n"
        "✅ Despliegue exitoso con volúmenes dedicados para data, log, tempdb y backup.\n"
        "- HADR habilitado mediante `MSSQL_ENABLE_HADR=1`.\n"
        f"- Réplicas: {cfg['replicas']}.\n\n"
        "## Parámetros\n"
        f"- Namespace: `{cfg['namespace']}`\n"
        f"- StatefulSet: `{cfg['app']}`\n"
        f"- Imagen: `{cfg['image']}`\n"
        f"- StorageClass: `{cfg['storage_class']}`\n"
        f"- Tamaño por PVC: `{cfg['storage_size']}`\n"
        f"- CPU/Memoria (QoS Guaranteed): `{cfg['cpu']}` / `{cfg['memory']}`\n"
        f"- Base de prueba: `{cfg['database']}`\n\n"
        "## Validación SQL\n"
        f"```text\n{version}\n```\n\n"
        "## Registro de prueba\n"
        f"```text\n{validation_output}\n```\n\n"
        "## Recursos\n"
        "### StatefulSet\n"
        f"```text\n{sts}\n```\n\n"
        "### Pods\n"
        f"```text\n{pods}\n```\n\n"
        "### PVCs (data, log, tempdb, backup)\n"
        f"```text\n{pvcs}\n```\n\n"
        "### Services\n"
        f"```text\n{svcs}\n```\n\n"
        "## Conexión local\n"
        f"```bash\nkubectl -n {cfg['namespace']} port-forward svc/{cfg['service']} 1433:1433\n```\n"
        "Conectar con `localhost,1433`, usuario `sa`.\n\n"
        "## Nota sobre tempdb\n"
        "El directorio `/var/opt/mssql/tempdb` está montado en un volumen independiente. "
        "Para mover realmente los archivos de tempdb a ese volumen, ejecuta los comandos SQL de reubicación después del despliegue.\n"
    )
    report.write_text(content, encoding="utf-8")
    return report

def parse_args():
    p = argparse.ArgumentParser(description="Despliega SQL Server con 4 volúmenes separados (data, log, tempdb, backup)")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p.add_argument("--app", default=DEFAULT_APP)
    p.add_argument("--secret", default=DEFAULT_SECRET)
    p.add_argument("--headless-service", default=DEFAULT_HEADLESS_SERVICE)
    p.add_argument("--service", default=DEFAULT_SERVICE)
    p.add_argument("--storage-class", default=DEFAULT_STORAGE_CLASS)
    p.add_argument("--storage-size", default=DEFAULT_STORAGE_SIZE)
    p.add_argument("--image", default=DEFAULT_IMAGE)
    p.add_argument("--database", default=DEFAULT_DB)
    p.add_argument("--cpu", default=DEFAULT_CPU)
    p.add_argument("--memory", default=DEFAULT_MEMORY)
    p.add_argument("--replicas", type=int, default=DEFAULT_REPLICAS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--report-dir", default=".")
    p.add_argument("--delete-old-deployment", action="store_true")
    p.add_argument("--password-env", default="MSSQL_SA_PASSWORD")
    return p.parse_args()

def main():
    args = parse_args()
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl no encontrado")
    print("\n🚀 Despliegue SQL Server con 4 volúmenes separados (data, log, tempdb, backup)\n")
    if args.non_interactive:
        password = os.environ.get(args.password_env)
        if not password:
            raise RuntimeError(f"Modo no interactivo requiere variable {args.password_env}")
        cfg = {
            "namespace": args.namespace,
            "app": args.app,
            "secret": args.secret,
            "headless_service": args.headless_service,
            "service": args.service,
            "storage_class": args.storage_class,
            "storage_size": args.storage_size,
            "image": args.image,
            "database": args.database,
            "cpu": args.cpu,
            "memory": args.memory,
            "replicas": args.replicas,
            "password": password,
        }
    else:
        cfg = {
            "namespace": ask("Namespace", args.namespace),
            "app": ask("Nombre StatefulSet", args.app),
            "secret": ask("Secret", args.secret),
            "headless_service": ask("Service headless", args.headless_service),
            "service": ask("Service ClusterIP", args.service),
            "storage_class": ask("StorageClass", args.storage_class),
            "storage_size": ask("Tamaño para cada PVC (ej. 10Gi)", args.storage_size),
            "image": ask("Imagen SQL Server", args.image),
            "database": ask("Base de prueba", args.database),
            "cpu": ask("CPU request/limit", args.cpu),
            "memory": ask("Memoria request/limit", args.memory),
            "replicas": int(ask("Número de réplicas (1=una instancia)", str(args.replicas))),
            "password": ask("Password SA", secret=True),
        }
    for field in ("namespace", "app", "secret", "headless_service", "service"):
        validate_k8s_name(cfg[field], field)
    validate_sql_identifier(cfg["database"], "database")
    if not cfg["password"]:
        raise RuntimeError("Password requerida")
    print("\n📌 Validando acceso al clúster...")
    run(["kubectl", "cluster-info"])
    # Crear namespace
    kubectl_apply_yaml(f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {cfg['namespace']}\n")
    safe_delete_old_deployment(cfg["namespace"], cfg["app"], args.delete_old_deployment)
    create_or_update_secret(cfg["namespace"], cfg["secret"], cfg["password"])
    print("\n📦 Aplicando StatefulSet con 4 volúmenes...")
    kubectl_apply_yaml(build_manifests(cfg))
    wait_for_statefulset(cfg["namespace"], cfg["app"], args.timeout)
    wait_for_sql_ready(cfg["namespace"], cfg["app"], args.timeout)
    pod = get_running_pod(cfg["namespace"], cfg["app"])
    print(f"✅ Pod activo: {pod}")
    print("\n🧪 Probando conexión...")
    version = exec_sql(cfg["namespace"], cfg["app"], cfg["password"], "SELECT @@VERSION;").stdout
    print(version)
    validation = create_test_db_and_record(cfg["namespace"], cfg["app"], cfg["password"], cfg["database"])
    print(validation)
    print("\n📄 Generando reporte...")
    report = generate_report(cfg, validation, args.report_dir)
    print(f"✅ Reporte: {report.resolve()}")
    print("\n🎉 Despliegue completado.")
    print(f"Para exponer localmente: kubectl -n {cfg['namespace']} port-forward svc/{cfg['service']} 1433:1433")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
