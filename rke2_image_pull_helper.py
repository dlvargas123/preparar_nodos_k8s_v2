#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import datetime
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path


CTR_SOCKET = "/run/k3s/containerd/containerd.sock"
CTR_NAMESPACE = "k8s.io"

CTR_DEFAULT = "/var/lib/rancher/rke2/bin/ctr"
KUBECTL_DEFAULT = "/var/lib/rancher/rke2/bin/kubectl"

HOSTS_DIR = (
    "/var/lib/rancher/rke2/"
    "agent/etc/containerd/certs.d"
)

KUBECONFIG_DEFAULT = (
    "/etc/rancher/rke2/rke2.yaml"
)

GODEBUG_VALUE = "tlsmlkem=0"

FAIL_REASONS = {
    "ImagePullBackOff",
    "ErrImagePull",
}

REPORT_DIR = Path(
    "/root/rke2-image-recovery"
)


def run(
    cmd,
    timeout=120,
    env=None,
):
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )

    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd,
            124,
            "",
            "TIMEOUT",
        )


def find_program(
    name,
    default_path,
):
    p = run(
        [
            "sh",
            "-c",
            "command -v %s"
            % shlex.quote(name),
        ],
        timeout=5,
    )

    if (
        p.returncode == 0
        and p.stdout.strip()
    ):
        return p.stdout.strip()

    if (
        Path(default_path).is_file()
        and os.access(
            default_path,
            os.X_OK,
        )
    ):
        return default_path

    return None


def kube_environment():
    env = os.environ.copy()

    if (
        not env.get("KUBECONFIG")
        and Path(
            KUBECONFIG_DEFAULT
        ).is_file()
    ):
        env["KUBECONFIG"] = (
            KUBECONFIG_DEFAULT
        )

    return env


def local_hostnames():
    result = set()

    hostname = (
        socket.gethostname()
        .lower()
        .strip()
    )

    if hostname:
        result.add(hostname)
        result.add(
            hostname.split(".")[0]
        )

    p = run(
        ["hostname", "-s"],
        timeout=5,
    )

    if (
        p.returncode == 0
        and p.stdout.strip()
    ):
        result.add(
            p.stdout
            .strip()
            .lower()
        )

    return result


def local_ips():
    p = run(
        ["hostname", "-I"],
        timeout=5,
    )

    if p.returncode != 0:
        return set()

    return {
        x.strip()
        for x
        in p.stdout.split()
        if x.strip()
    }


def detect_node(
    kubectl,
    env,
):
    p = run(
        [
            kubectl,
            "get",
            "nodes",
            "-o",
            "json",
        ],
        timeout=30,
        env=env,
    )

    if p.returncode != 0:
        raise RuntimeError(
            "No puedo consultar Nodes:\n%s"
            % p.stderr.strip()
        )

    data = json.loads(
        p.stdout
    )

    hostnames = (
        local_hostnames()
    )

    ips = local_ips()

    #
    # Primero por nombre/hostname.
    #
    for node in data.get(
        "items",
        [],
    ):

        name = (
            node.get(
                "metadata",
                {},
            )
            .get(
                "name",
                "",
            )
        )

        if (
            name.lower()
            in hostnames
        ):
            return name

        for address in (
            node.get(
                "status",
                {},
            )
            .get(
                "addresses",
                [],
            )
        ):

            if (
                address.get("type")
                == "Hostname"
                and
                address.get(
                    "address",
                    "",
                ).lower()
                in hostnames
            ):
                return name

    #
    # Segundo intento por IP.
    #
    for node in data.get(
        "items",
        [],
    ):

        name = (
            node.get(
                "metadata",
                {},
            )
            .get(
                "name",
                "",
            )
        )

        for address in (
            node.get(
                "status",
                {},
            )
            .get(
                "addresses",
                [],
            )
        ):

            if (
                address.get("type")
                in (
                    "InternalIP",
                    "ExternalIP",
                )
                and
                address.get(
                    "address"
                )
                in ips
            ):
                return name

    raise RuntimeError(
        "No pude asociar este host "
        "con un Node Kubernetes."
    )


def pod_spec_images(
    pod,
):
    result = {}

    spec = (
        pod.get(
            "spec",
            {},
        )
        or {}
    )

    sections = (
        (
            "containers",
            "container",
        ),
        (
            "initContainers",
            "initContainer",
        ),
        (
            "ephemeralContainers",
            "ephemeralContainer",
        ),
    )

    for section, kind in sections:

        for item in (
            spec.get(
                section,
                [],
            )
            or []
        ):

            name = item.get(
                "name",
                "",
            )

            if not name:
                continue

            result[
                (kind, name)
            ] = {
                "image":
                    item.get(
                        "image",
                        "",
                    ),

                "policy":
                    item.get(
                        "imagePullPolicy",
                        "",
                    ),
            }

    return result


def get_failed_images(
    kubectl,
    env,
    node,
):
    p = run(
        [
            kubectl,
            "get",
            "pods",
            "-A",
            "--field-selector",
            "spec.nodeName=%s"
            % node,
            "-o",
            "json",
        ],
        timeout=60,
        env=env,
    )

    if p.returncode != 0:
        raise RuntimeError(
            "Error consultando Pods:\n%s"
            % p.stderr.strip()
        )

    data = json.loads(
        p.stdout
    )

    failures = []

    groups = (
        (
            "containerStatuses",
            "container",
        ),
        (
            "initContainerStatuses",
            "initContainer",
        ),
        (
            "ephemeralContainerStatuses",
            "ephemeralContainer",
        ),
    )

    for pod in data.get(
        "items",
        [],
    ):

        metadata = (
            pod.get(
                "metadata",
                {},
            )
            or {}
        )

        status = (
            pod.get(
                "status",
                {},
            )
            or {}
        )

        images = (
            pod_spec_images(
                pod
            )
        )

        for (
            status_section,
            kind,
        ) in groups:

            for container in (
                status.get(
                    status_section,
                    [],
                )
                or []
            ):

                waiting = (
                    container.get(
                        "state",
                        {},
                    )
                    .get(
                        "waiting",
                        {},
                    )
                    or {}
                )

                reason = (
                    waiting.get(
                        "reason",
                        "",
                    )
                )

                if (
                    reason
                    not in FAIL_REASONS
                ):
                    continue

                container_name = (
                    container.get(
                        "name",
                        "",
                    )
                )

                spec_info = (
                    images.get(
                        (
                            kind,
                            container_name,
                        ),
                        {},
                    )
                )

                image = (
                    spec_info.get(
                        "image"
                    )
                    or
                    container.get(
                        "image",
                        "",
                    )
                )

                failures.append(
                    {
                        "namespace":
                            metadata.get(
                                "namespace",
                                "",
                            ),

                        "pod":
                            metadata.get(
                                "name",
                                "",
                            ),

                        "node":
                            node,

                        "container":
                            container_name,

                        "type":
                            kind,

                        "image":
                            image,

                        "policy":
                            spec_info.get(
                                "policy",
                                "",
                            ),

                        "reason":
                            reason,

                        "message":
                            waiting.get(
                                "message",
                                "",
                            ),
                    }
                )

    return failures


def print_failures_table(
    failures,
):
    print()
    print("=" * 190)

    print(
        "%-22s %-46s %-27s "
        "%-12s %-18s %s"
        % (
            "NAMESPACE",
            "POD",
            "CONTAINER",
            "POLICY",
            "REASON",
            "IMAGE",
        )
    )

    print("-" * 190)

    for item in failures:

        print(
            "%-22s %-46s %-27s "
            "%-12s %-18s %s"
            % (
                item[
                    "namespace"
                ][:22],

                item[
                    "pod"
                ][:46],

                item[
                    "container"
                ][:27],

                item[
                    "policy"
                ][:12],

                item[
                    "reason"
                ][:18],

                item[
                    "image"
                ],
            )
        )

    print("=" * 190)


def ctr_pull_help(
    ctr,
):
    p = run(
        [
            ctr,
            "images",
            "pull",
            "--help",
        ],
        timeout=15,
    )

    return (
        (p.stdout or "")
        +
        (p.stderr or "")
    )


def pull_image(
    ctr,
    help_text,
    image,
):
    env = os.environ.copy()

    env[
        "GODEBUG"
    ] = GODEBUG_VALUE

    cmd = [
        ctr,
        "--address",
        CTR_SOCKET,
        "--namespace",
        CTR_NAMESPACE,
        "images",
        "pull",
    ]

    #
    # Mantiene configuración de mirrors
    # de containerd/RKE2 cuando aplica.
    #
    if (
        "--hosts-dir"
        in help_text
        and
        Path(
            HOSTS_DIR
        ).is_dir()
    ):
        cmd.extend(
            [
                "--hosts-dir",
                HOSTS_DIR,
            ]
        )

    #
    # Requerimiento principal:
    # no validar certificado TLS.
    #
    if (
        "--skip-verify"
        not in help_text
    ):

        return {
            "image":
                image,

            "status":
                "ERROR",

            "rc":
                98,

            "error":
                (
                    "ctr no soporta "
                    "--skip-verify"
                ),
        }

    cmd.append(
        "--skip-verify"
    )

    if (
        "--local"
        in help_text
    ):
        cmd.append(
            "--local"
        )

    cmd.append(
        image
    )

    print()
    print("-" * 120)

    print(
        "IMAGEN      : %s"
        % image
    )

    print(
        "TLS VERIFY  : OMITIDO"
    )

    print(
        "GODEBUG     : %s"
        % GODEBUG_VALUE
    )

    print(
        "COMANDO     : %s"
        % " ".join(
            shlex.quote(x)
            for x in cmd
        )
    )

    print("-" * 120)

    p = run(
        cmd,
        timeout=900,
        env=env,
    )

    if p.stdout.strip():
        print(
            p.stdout.strip()
        )

    if p.stderr.strip():
        print(
            p.stderr.strip()
        )

    if (
        p.returncode == 0
    ):

        print(
            "[OK] Descarga completada."
        )

        status = "OK"

    else:

        print(
            "[ERROR] Pull fallo. "
            "RC=%s"
            % p.returncode
        )

        status = "ERROR"

    error = (
        p.stderr.strip()
        or
        p.stdout.strip()
    )

    return {
        "image":
            image,

        "status":
            status,

        "rc":
            p.returncode,

        "error":
            error[-4000:],
    }


def classify_error(
    text,
):
    value = (
        text
        or ""
    ).lower()

    if (
        "x509" in value
        or
        "certificate" in value
        or
        "tls" in value
    ):
        return "TLS/CERTIFICADO"

    if (
        "no such host"
        in value
        or
        "name resolution"
        in value
    ):
        return "DNS"

    if (
        "timeout"
        in value
        or
        "deadline exceeded"
        in value
    ):
        return "TIMEOUT/FIREWALL"

    if (
        "connection refused"
        in value
    ):
        return "CONEXION_RECHAZADA"

    if (
        "unauthorized"
        in value
        or
        "401"
        in value
    ):
        return "AUTH_401"

    if (
        "forbidden"
        in value
        or
        "403"
        in value
    ):
        return "AUTH_403"

    if (
        "not found"
        in value
        or
        "404"
        in value
    ):
        return "IMAGE_NOT_FOUND"

    return "OTRO"


def create_report(
    node,
    failures_before,
    pull_results,
    failures_after,
):
    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = (
        datetime.datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    txt_file = (
        REPORT_DIR
        /
        (
            "recovery_%s_%s.txt"
            % (
                node,
                stamp,
            )
        )
    )

    csv_file = (
        REPORT_DIR
        /
        (
            "recovery_%s_%s.csv"
            % (
                node,
                stamp,
            )
        )
    )

    unique_before = {}

    for item in failures_before:

        image = item[
            "image"
        ]

        unique_before.setdefault(
            image,
            [],
        )

        unique_before[
            image
        ].append(
            item
        )

    results_by_image = {
        x["image"]: x
        for x
        in pull_results
    }

    remaining_images = {
        x["image"]
        for x
        in failures_after
    }

    ok = 0
    error = 0

    rows = []

    for image in sorted(
        unique_before
    ):

        refs = (
            unique_before[
                image
            ]
        )

        result = (
            results_by_image.get(
                image,
                {},
            )
        )

        policies = sorted(
            {
                x["policy"]
                for x in refs
                if x["policy"]
            }
        )

        pull_status = (
            result.get(
                "status",
                "NO_EJECUTADO",
            )
        )

        if (
            pull_status == "OK"
        ):
            ok += 1
        else:
            error += 1

        rows.append(
            {
                "node":
                    node,

                "image":
                    image,

                "pods_afectados":
                    len(
                        {
                            (
                                x[
                                    "namespace"
                                ],
                                x[
                                    "pod"
                                ],
                            )
                            for x
                            in refs
                        }
                    ),

                "pull_policy":
                    ",".join(
                        policies
                    )
                    or "?",

                "pull_manual":
                    pull_status,

                "rc":
                    result.get(
                        "rc",
                        "",
                    ),

                "sigue_en_error_k8s":
                    (
                        "SI"
                        if image
                        in remaining_images
                        else "NO"
                    ),

                "diagnostico":
                    classify_error(
                        result.get(
                            "error",
                            "",
                        )
                    ),

                "detalle":
                    result.get(
                        "error",
                        "",
                    )
                    .replace(
                        "\n",
                        " ",
                    )[:1500],
            }
        )

    fields = [
        "node",
        "image",
        "pods_afectados",
        "pull_policy",
        "pull_manual",
        "rc",
        "sigue_en_error_k8s",
        "diagnostico",
        "detalle",
    ]

    with csv_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as fh:

        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(
            rows
        )

    remaining = len(
        {
            x["image"]
            for x
            in failures_after
        }
    )

    always = len(
        {
            x["image"]
            for x
            in failures_before
            if x["policy"]
            == "Always"
        }
    )

    lines = []

    lines.append(
        "=" * 100
    )

    lines.append(
        " REPORTE EJECUTIVO - "
        "RKE2 IMAGE PULL RECOVERY"
    )

    lines.append(
        "=" * 100
    )

    lines.append(
        "Nodo                         : %s"
        % node
    )

    lines.append(
        "Fecha                        : %s"
        % datetime.datetime.now()
        .strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    lines.append(
        "Contenedores con error inicial: %s"
        % len(
            failures_before
        )
    )

    lines.append(
        "Imagenes unicas afectadas    : %s"
        % len(
            unique_before
        )
    )

    lines.append(
        "Pull manual exitoso          : %s"
        % ok
    )

    lines.append(
        "Pull manual fallido          : %s"
        % error
    )

    lines.append(
        "Imagenes aun en error K8s    : %s"
        % remaining
    )

    lines.append(
        "Imagenes PullPolicy=Always   : %s"
        % always
    )

    lines.append("")
    lines.append(
        "DETALLE POR IMAGEN"
    )

    lines.append(
        "-" * 100
    )

    for row in rows:

        lines.append(
            "[%-5s] %-80s "
            "K8S=%-3s DIAG=%s"
            % (
                row[
                    "pull_manual"
                ],
                row[
                    "image"
                ],
                row[
                    "sigue_en_error_k8s"
                ],
                row[
                    "diagnostico"
                ],
            )
        )

    lines.append("")
    lines.append(
        "-" * 100
    )

    if error == 0:

        lines.append(
            "RESULTADO EJECUTIVO: "
            "TODAS LAS IMAGENES "
            "FUERON DESCARGADAS."
        )

    else:

        lines.append(
            "RESULTADO EJECUTIVO: "
            "RECUPERACION PARCIAL. "
            "%s IMAGEN(ES) FALLARON."
            % error
        )

    if remaining:

        lines.append(
            "OBSERVACION: Kubernetes "
            "todavia reporta imagenes "
            "en backoff. Puede requerir "
            "esperar el siguiente retry "
            "del kubelet."
        )

    if always:

        lines.append(
            "ADVERTENCIA: existen imagenes "
            "con imagePullPolicy=Always. "
            "El preload manual puede no "
            "resolver futuros reinicios; "
            "debe corregirse la configuracion "
            "TLS/registry permanentemente."
        )

    lines.append("")
    lines.append(
        "NOTA DE SEGURIDAD: "
        "--skip-verify mantiene HTTPS/TLS, "
        "pero NO valida el certificado."
    )

    lines.append("")
    lines.append(
        "CSV: %s"
        % csv_file
    )

    lines.append(
        "TXT: %s"
        % txt_file
    )

    lines.append(
        "=" * 100
    )

    text = (
        "\n".join(
            lines
        )
        + "\n"
    )

    txt_file.write_text(
        text,
        encoding="utf-8",
    )

    return (
        text,
        txt_file,
        csv_file,
        error,
    )


def main():
    print(
        "=" * 120
    )

    print(
        " RKE2 - AUTO RECOVERY "
        "DE ImagePullBackOff / ErrImagePull"
    )

    print(
        "=" * 120
    )

    if (
        os.geteuid()
        != 0
    ):
        print(
            "[FATAL] Ejecuta como root."
        )
        return 1

    kubectl = find_program(
        "kubectl",
        KUBECTL_DEFAULT,
    )

    if not kubectl:

        print(
            "[FATAL] No encontre kubectl."
        )

        return 1

    ctr = find_program(
        "ctr",
        CTR_DEFAULT,
    )

    if not ctr:

        print(
            "[FATAL] No encontre ctr."
        )

        return 1

    if not Path(
        CTR_SOCKET
    ).exists():

        print(
            "[FATAL] No existe socket: %s"
            % CTR_SOCKET
        )

        return 1

    env = (
        kube_environment()
    )

    api = run(
        [
            kubectl,
            "get",
            "--raw=/version",
        ],
        timeout=15,
        env=env,
    )

    if (
        api.returncode
        != 0
    ):

        print(
            "[FATAL] Este nodo no "
            "puede consultar Kubernetes API."
        )

        print(
            api.stderr.strip()
        )

        return 1

    node = detect_node(
        kubectl,
        env,
    )

    print(
        "NODE LOCAL : %s"
        % node
    )

    print(
        "CTR        : %s"
        % ctr
    )

    print(
        "SOCKET     : %s"
        % CTR_SOCKET
    )

    print()
    print(
        "[1/4] Buscando errores "
        "de descarga SOLO en este nodo..."
    )

    failures = get_failed_images(
        kubectl,
        env,
        node,
    )

    if not failures:

        print()
        print(
            "[OK] Este nodo no tiene "
            "ImagePullBackOff ni "
            "ErrImagePull."
        )

        print(
            "[OK] No se realizo "
            "ninguna descarga."
        )

        return 0

    print_failures_table(
        failures
    )

    images = []

    for item in failures:

        image = item[
            "image"
        ]

        if (
            image
            and image
            not in images
        ):
            images.append(
                image
            )

    print()
    print(
        "[2/4] Imagenes unicas "
        "a recuperar: %s"
        % len(images)
    )

    for index, image in enumerate(
        images,
        1,
    ):

        print(
            "%02d. %s"
            % (
                index,
                image,
            )
        )

    help_text = (
        ctr_pull_help(
            ctr
        )
    )

    if (
        "--skip-verify"
        not in help_text
    ):

        print()
        print(
            "[FATAL] Esta version "
            "de ctr no soporta "
            "--skip-verify."
        )

        return 1

    print()
    print(
        "[3/4] Iniciando "
        "recuperacion automatica..."
    )

    pull_results = []

    for image in images:

        result = pull_image(
            ctr,
            help_text,
            image,
        )

        pull_results.append(
            result
        )

    print()
    print(
        "[INFO] Esperando 15 segundos "
        "para que kubelet reprocese "
        "los Pods..."
    )

    time.sleep(15)

    print()
    print(
        "[4/4] Verificando estado "
        "final del nodo..."
    )

    failures_after = (
        get_failed_images(
            kubectl,
            env,
            node,
        )
    )

    (
        report,
        txt_file,
        csv_file,
        errors,
    ) = create_report(
        node,
        failures,
        pull_results,
        failures_after,
    )

    print()
    print(
        report
    )

    print(
        "Archivos generados:"
    )

    print(
        "  %s"
        % txt_file
    )

    print(
        "  %s"
        % csv_file
    )

    if failures_after:

        print()
        print(
            "ERRORES KUBERNETES "
            "QUE AUN APARECEN:"
        )

        print_failures_table(
            failures_after
        )

    return (
        0
        if errors == 0
        else 2
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )
