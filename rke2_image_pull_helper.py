#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

CTR = "/var/lib/rancher/rke2/bin/ctr"
SOCKET = "/run/k3s/containerd/containerd.sock"
NAMESPACE = "k8s.io"
GODEBUG_VALUE = "tlsmlkem=0"


def banner():
    print("=" * 80)
    print(" RKE2 IMAGE PULL HELPER")
    print("=" * 80)
    print(f"CTR       : {CTR}")
    print(f"SOCKET    : {SOCKET}")
    print(f"NAMESPACE : {NAMESPACE}")
    print(f"GODEBUG   : {GODEBUG_VALUE}")
    print("=" * 80)


def preflight():
    print("\n[1] Validando entorno...")

    if os.geteuid() != 0:
        print("[ERROR] Debes ejecutar como root.")
        sys.exit(1)

    if not Path(CTR).is_file():
        print(f"[ERROR] No existe: {CTR}")
        sys.exit(1)

    if not Path(SOCKET).exists():
        print(f"[ERROR] No existe socket: {SOCKET}")
        sys.exit(1)

    print("[OK] ctr encontrado")
    print("[OK] socket containerd encontrado")


def command_exists_skip_verify():
    result = subprocess.run(
        [CTR, "images", "pull", "--help"],
        text=True,
        capture_output=True
    )

    output = (result.stdout or "") + (result.stderr or "")
    return "--skip-verify" in output


def image_exists(image):
    cmd = [
        CTR,
        "--address", SOCKET,
        "--namespace", NAMESPACE,
        "images", "list"
    ]

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True
    )

    if result.returncode != 0:
        return False

    return image in result.stdout


def run_pull(image, skip_verify=False):
    env = os.environ.copy()
    env["GODEBUG"] = GODEBUG_VALUE

    cmd = [
        CTR,
        "--address", SOCKET,
        "--namespace", NAMESPACE,
        "images",
        "pull",
        "--local"
    ]

    if skip_verify:
        cmd.append("--skip-verify")

    cmd.append(image)

    print("\n" + "-" * 80)
    print(f"Imagen       : {image}")
    print(f"GODEBUG      : {GODEBUG_VALUE}")
    print(f"Skip verify  : {'SI' if skip_verify else 'NO'}")
    print("-" * 80)

    print("\nEjecutando:")
    print(" ".join(shlex.quote(x) for x in cmd))
    print("")

    try:
        result = subprocess.run(
            cmd,
            env=env,
            text=True
        )
    except KeyboardInterrupt:
        print("\n[WARN] Cancelado por usuario.")
        return False

    if result.returncode != 0:
        print(f"\n[ERROR] Pull falló. Código: {result.returncode}")
        return False

    print("\n[OK] Pull terminó correctamente.")

    if image_exists(image):
        print(f"[OK] Imagen encontrada en containerd: {image}")
        return True

    print("[WARN] El comando terminó OK pero no encontré la imagen en ctr images list.")
    return False


def ask_yes_no(text, default=False):
    default_text = "S/n" if default else "s/N"

    while True:
        value = input(f"{text} [{default_text}]: ").strip().lower()

        if value == "":
            return default

        if value in ("s", "si", "sí", "y", "yes"):
            return True

        if value in ("n", "no"):
            return False

        print("Responde s o n.")


def main():
    banner()
    preflight()

    supports_skip_verify = command_exists_skip_verify()

    if supports_skip_verify:
        print("[OK] ctr soporta --skip-verify")
    else:
        print("[WARN] ctr NO reporta soporte para --skip-verify")

    print("\nPuedes ingresar varias imágenes.")
    print("Escribe una imagen y presiona ENTER.")
    print("Cuando termines escribe: fin")
    print("")
    print("Ejemplos:")
    print(" registry.rancher.com/rancher/fleet-agent:v0.14.2")
    print(" registry.rancher.com/rancher/cluster-api-controller:v1.10.6")
    print("")

    images = []

    while True:
        try:
            image = input("imagen> ").strip()
        except KeyboardInterrupt:
            print("\nCancelado.")
            sys.exit(0)

        if not image:
            continue

        if image.lower() in ("fin", "salir", "exit"):
            break

        if image not in images:
            images.append(image)
        else:
            print("[INFO] Esa imagen ya fue agregada.")

    if not images:
        print("\n[INFO] No ingresaste imágenes.")
        sys.exit(0)

    print("\nImágenes seleccionadas:")
    for i, image in enumerate(images, 1):
        print(f" {i}. {image}")

    print("\nModo de descarga:")
    print(" 1 = GODEBUG=tlsmlkem=0")
    print(" 2 = GODEBUG=tlsmlkem=0 + --skip-verify")
    print("")

    while True:
        mode = input("Selecciona modo [1]: ").strip()

        if mode == "":
            mode = "1"

        if mode in ("1", "2"):
            break

        print("Selecciona 1 o 2.")

    skip_verify = mode == "2"

    if skip_verify:
        if not supports_skip_verify:
            print("\n[ERROR] Tu ctr no soporta --skip-verify.")
            sys.exit(1)

        print("\nATENCION:")
        print("--skip-verify desactiva la validación del certificado TLS.")
        print("Úsalo únicamente cuando sea necesario.")

        if not ask_yes_no("¿Continuar?", False):
            print("Cancelado.")
            sys.exit(0)

    print("\n" + "=" * 80)
    print(" INICIANDO DESCARGAS")
    print("=" * 80)

    results = []

    for image in images:

        if image_exists(image):
            print(f"\n[INFO] Ya existe localmente:")
            print(f"       {image}")

            if not ask_yes_no("¿Quieres volver a descargarla?", False):
                results.append((image, True))
                continue

        success = run_pull(
            image=image,
            skip_verify=skip_verify
        )

        results.append((image, success))

        if not success:
            print("\n[WARN] La imagen falló.")

            if ask_yes_no("¿Quieres reintentar esta imagen?", True):
                success = run_pull(
                    image=image,
                    skip_verify=skip_verify
                )

                results[-1] = (image, success)

    print("\n")
    print("=" * 80)
    print(" RESUMEN FINAL")
    print("=" * 80)

    failures = 0

    for image, success in results:
        if success:
            print(f"[OK]    {image}")
        else:
            print(f"[ERROR] {image}")
            failures += 1

    print("=" * 80)

    if failures == 0:
        print("\n[OK] Todas las imágenes quedaron disponibles en containerd.")
    else:
        print(f"\n[WARN] Fallaron {failures} imagen(es).")

    print("\nValidación manual:")
    print(
        f"{CTR} --address {SOCKET} "
        f"--namespace {NAMESPACE} images list"
    )


if __name__ == "__main__":
    main()
