#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sftpgo_folderdate_to_minio_flat_inbound_robust_report.py

- Selecciona carpeta objetivo por fecha: remote_root/YYYYMMDD (ayer según TZ).
- Si no existe la carpeta de ayer, puede usar fallback a la carpeta más reciente (opcional).
- Dentro de la carpeta objetivo:
  - Solo archivos regulares
  - Solo si el nombre contiene INBOUND (configurable; ignore-case opcional)
- Sube a MinIO/S3:
  - SIN estructura (aplanado)
  - Anti-colisión: agrega hash corto basado en rel_path
- Resistente:
  - retries + backoff
  - reconexión SFTP en fallos
  - keepalive
  - upload streaming (sin archivo temporal)
  - verificación por tamaño (ContentLength)
  - state-file para reanudar sin repetir
- Reporte en texto en cada ejecución (resumen + top ejemplos).
"""

import argparse
import os
import sys
import stat
import json
import time
import logging
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, Any, List

import paramiko
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError, ConnectionClosedError


# =========================
# Configs
# =========================
@dataclass
class SftpConfig:
    host: str
    port: int
    username: str
    password: str
    remote_root: str
    timeout: int
    keepalive: int


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    secure: bool
    verify_tls: bool
    prefix: str


# =========================
# Logging
# =========================
def setup_logger(verbose: bool, log_file: Optional[str]) -> logging.Logger:
    logger = logging.getLogger("sftp_folderdate_to_s3_flat_filter_robust")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("[%(levelname)s] %(message)s")

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)

    logger.handlers = [sh]

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG if verbose else logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# =========================
# S3 helpers
# =========================
def parse_endpoint(endpoint: str, secure: bool) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return ("https://" if secure else "http://") + endpoint


def s3_client(cfg: S3Config):
    # retries nativos de botocore + los nuestros alrededor
    return boto3.client(
        "s3",
        endpoint_url=parse_endpoint(cfg.endpoint, cfg.secure),
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 10, "mode": "standard"}),
        verify=cfg.verify_tls,
    )


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def verify_uploaded_size(s3, bucket: str, key: str, expected_size: int) -> bool:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return int(head.get("ContentLength", -1)) == int(expected_size)
    except Exception:
        return False


# =========================
# Generic retry
# =========================
def retry(op_name: str, fn, logger: logging.Logger, attempts: int = 5, base_sleep: float = 0.8,
          retry_exceptions: Tuple[type, ...] = (Exception,)):
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except retry_exceptions as e:
            last_exc = e
            if i == attempts:
                logger.error(f"{op_name} falló tras {attempts} intentos: {e}")
                raise
            sleep_s = base_sleep * (2 ** (i - 1))
            logger.warning(f"{op_name} falló (intento {i}/{attempts}): {e} | reintento en {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise last_exc  # pragma: no cover


# =========================
# SFTP connect + reconnect
# =========================
def connect_sftp(cfg: SftpConfig, logger: logging.Logger) -> Tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _do_connect():
        ssh.connect(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
            timeout=cfg.timeout,
            banner_timeout=cfg.timeout,
            auth_timeout=cfg.timeout,
        )
        transport = ssh.get_transport()
        if transport:
            transport.set_keepalive(cfg.keepalive)
        return ssh.open_sftp()

    sftp = retry("Conexión SFTP", _do_connect, logger, attempts=5, base_sleep=1.0, retry_exceptions=(Exception,))
    return ssh, sftp


def safe_close(obj):
    try:
        obj.close()
    except Exception:
        pass


# =========================
# State file (resume)
# =========================
def load_state(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path: str, state: Dict[str, Any]):
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# =========================
# Remote folder selection (YYYYMMDD)
# =========================
def is_yyyymmdd(name: str) -> bool:
    return len(name) == 8 and name.isdigit()


def sftp_path_exists_dir(sftp, path: str) -> bool:
    try:
        st = sftp.stat(path)
        return stat.S_ISDIR(st.st_mode)
    except Exception:
        return False


def list_date_dirs(sftp, remote_root: str, logger: logging.Logger) -> List[str]:
    """
    Lista subdirectorios YYYYMMDD bajo remote_root.
    Devuelve lista de nombres (no rutas completas).
    """
    remote_root = remote_root.rstrip("/")

    def _list():
        return sftp.listdir_attr(remote_root)

    entries = retry(f"Listar {remote_root}", _list, logger, attempts=4, base_sleep=0.6,
                    retry_exceptions=(IOError, OSError, EOFError, paramiko.SSHException))
    dirs = []
    for ent in entries:
        if stat.S_ISDIR(ent.st_mode) and is_yyyymmdd(ent.filename):
            dirs.append(ent.filename)
    return sorted(dirs)


def choose_target_folder(sftp, remote_root: str, yesterday_str: str, allow_fallback_latest: bool,
                         logger: logging.Logger) -> Tuple[str, str]:
    """
    Retorna (target_dir_full_path, chosen_label)
    chosen_label: 'yesterday' o 'latest'
    """
    remote_root = remote_root.rstrip("/")
    target = f"{remote_root}/{yesterday_str}"

    if sftp_path_exists_dir(sftp, target):
        return target, "yesterday"

    if not allow_fallback_latest:
        return target, "yesterday_missing"

    dirs = list_date_dirs(sftp, remote_root, logger)
    if not dirs:
        return target, "no_date_dirs"

    latest = dirs[-1]
    return f"{remote_root}/{latest}", "latest"


# =========================
# Listing files in chosen folder (no full tree needed)
# =========================
def list_files_in_folder(sftp, folder: str, logger: logging.Logger, max_errors: int = 50):
    """
    Lista RECURSIVO dentro de 'folder' (por si hay subcarpetas internas).
    Retorna tuplas: (remote_path, rel_path, mtime, size)
    rel_path relativo a 'folder' (para hash anti-colisión).
    """
    folder = folder.rstrip("/")
    stack = [folder]
    errors = 0

    while stack:
        cur = stack.pop()

        def _listdir():
            return sftp.listdir_attr(cur)

        try:
            entries = retry(f"Listar {cur}", _listdir, logger, attempts=4, base_sleep=0.5,
                            retry_exceptions=(IOError, OSError, EOFError, paramiko.SSHException))
        except Exception as e:
            errors += 1
            logger.warning(f"No pude listar {cur}: {e}")
            if errors >= max_errors:
                logger.error("Demasiados errores listando. Corto el recorrido.")
                return
            continue

        for ent in entries:
            rpath = f"{cur}/{ent.filename}"
            mode = ent.st_mode

            if stat.S_ISDIR(mode):
                stack.append(rpath)
                continue

            if not stat.S_ISREG(mode):
                continue

            mtime = int(ent.st_mtime)
            size = int(ent.st_size)

            rel = rpath[len(folder) + 1:] if rpath.startswith(folder + "/") else ent.filename
            yield rpath, rel, mtime, size


# =========================
# Core filters + upload
# =========================
def name_matches(filename: str, needle: str, ignore_case: bool) -> bool:
    if ignore_case:
        return needle.lower() in filename.lower()
    return needle in filename


def flat_name_with_hash(remote_path: str, rel_path: str) -> str:
    base = os.path.basename(remote_path)
    name, ext = os.path.splitext(base)
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:8]
    return f"{name}__{h}{ext}"


def upload_streaming_sftp_to_s3(sftp, s3, s3cfg: S3Config, remote_path: str, key: str,
                                logger: logging.Logger, attempts: int = 5):
    def _upload():
        with sftp.open(remote_path, "rb") as rf:
            s3.upload_fileobj(rf, s3cfg.bucket, key)
        return True

    retry(f"Upload {remote_path}", _upload, logger, attempts=attempts, base_sleep=1.0,
          retry_exceptions=(Exception,))


# =========================
# Report
# =========================
def default_report_name() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"report_{ts}.txt"


def write_report(report_file: str, append: bool, lines: List[str]):
    mode = "a" if append else "w"
    with open(report_file, mode, encoding="utf-8") as f:
        for ln in lines:
            f.write(ln.rstrip("\n") + "\n")


# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser(
        description="Copia a MinIO (S3) desde SFTPGo por carpeta YYYYMMDD, SIN estructura, filtrando INBOUND, robusto + reporte."
    )

    # SFTP
    ap.add_argument("--sftp-host", default="10.0.0.155")
    ap.add_argument("--sftp-port", type=int, default=30127)
    ap.add_argument("--sftp-user", default="voip-ifx")
    ap.add_argument("--sftp-pass", default="voip-ifx")
    ap.add_argument("--remote-root", default="/recordings")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--keepalive", type=int, default=15)

    # S3
    ap.add_argument("--s3-endpoint", default="10.0.0.155:9000")
    ap.add_argument("--s3-bucket", default="test-voip-ifx")
    ap.add_argument("--s3-access-key", default="test-voip-ifx")
    ap.add_argument("--s3-secret-key", default="test-voip-ifx")
    ap.add_argument("--s3-prefix", default="")
    ap.add_argument("--secure", action="store_true")
    ap.add_argument("--no-verify-tls", action="store_true")

    # Operación
    ap.add_argument("--tz", default="America/Bogota")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-files", type=int, default=0, help="0 = sin límite.")
    ap.add_argument("--state-file", default="", help="JSON para reanudar (guarda keys subidos).")
    ap.add_argument("--log-file", default="", help="Log adicional a archivo.")
    ap.add_argument("-v", "--verbose", action="store_true")

    # Filtro por nombre
    ap.add_argument("--name-contains", default="INBOUND")
    ap.add_argument("--ignore-case", action="store_true")

    # Selección carpeta
    ap.add_argument("--fallback-latest", action="store_true",
                    help="Si no existe la carpeta de AYER, usa la carpeta YYYYMMDD más reciente disponible.")

    # Reporte
    ap.add_argument("--report-file", default="", help="Archivo de reporte. Si no se da, crea report_YYYYMMDD_HHMMSS.txt")
    ap.add_argument("--report-append", action="store_true", help="Anexa al reporte en vez de sobrescribir.")

    args = ap.parse_args()

    report_file = args.report_file.strip() if args.report_file.strip() else default_report_name()

    logger = setup_logger(args.verbose, args.log_file if args.log_file else None)

    # Calcular AYER en formato carpeta YYYYMMDD
    tz = ZoneInfo(args.tz)
    now = datetime.now(tz=tz)
    yesterday = (now - timedelta(days=1)).date()
    yesterday_str = yesterday.strftime("%Y%m%d")

    sftp_cfg = SftpConfig(
        host=args.sftp_host,
        port=args.sftp_port,
        username=args.sftp_user,
        password=args.sftp_pass,
        remote_root=args.remote_root,
        timeout=args.timeout,
        keepalive=args.keepalive,
    )
    s3_cfg = S3Config(
        endpoint=args.s3_endpoint,
        bucket=args.s3_bucket,
        access_key=args.s3_access_key,
        secret_key=args.s3_secret_key,
        secure=args.secure,
        verify_tls=not args.no_verify_tls,
        prefix=args.s3_prefix.lstrip("/"),
    )

    s3 = s3_client(s3_cfg)

    state = load_state(args.state_file)
    done_keys = set(state.get("done_keys", []))

    # Conectar SFTP
    ssh, sftp = connect_sftp(sftp_cfg, logger)

    def reconnect():
        nonlocal ssh, sftp
        logger.warning("Reconectando SFTP...")
        safe_close(sftp)
        safe_close(ssh)
        ssh, sftp = connect_sftp(sftp_cfg, logger)

    # Stats + samples para reporte
    start_ts = datetime.now(tz=tz)
    chosen_folder = ""
    chosen_mode = ""
    total_files_seen = 0
    matched_name = 0
    uploaded = 0
    skipped_exists = 0
    skipped_state = 0
    failed_uploads = 0
    sample_uploaded: List[str] = []
    sample_skipped_exists: List[str] = []
    sample_skipped_name: List[str] = []
    sample_failed: List[str] = []

    try:
        # Seleccionar folder objetivo
        try:
            chosen_folder, chosen_mode = choose_target_folder(
                sftp=sftp,
                remote_root=sftp_cfg.remote_root,
                yesterday_str=yesterday_str,
                allow_fallback_latest=args.fallback_latest,
                logger=logger,
            )
        except (EOFError, OSError, IOError, paramiko.SSHException) as e:
            logger.warning(f"Fallo al seleccionar carpeta objetivo: {e}")
            reconnect()
            chosen_folder, chosen_mode = choose_target_folder(
                sftp=sftp,
                remote_root=sftp_cfg.remote_root,
                yesterday_str=yesterday_str,
                allow_fallback_latest=args.fallback_latest,
                logger=logger,
            )

        logger.info(f"Carpeta objetivo: {chosen_folder} (modo={chosen_mode})")
        logger.info(f"Filtro nombre: contiene '{args.name_contains}' (ignore_case={args.ignore_case})")
        logger.info("Modo: plano (sin estructura) + hash anti-colisión por rel_path")
        if args.dry_run:
            logger.info("DRY-RUN: no se subirá nada.")
        if args.state_file:
            logger.info(f"State file: {args.state_file}")
        logger.info(f"Reporte: {report_file} (append={args.report_append})")

        # Si la carpeta de ayer no existe y no hay fallback, igual intentamos listar y quedará vacío.
        # Recorremos archivos del folder elegido (recursivo por si hay subcarpetas)
        for remote_path, rel_path, mtime, size in list_files_in_folder(sftp, chosen_folder, logger):
            total_files_seen += 1

            filename = os.path.basename(remote_path)
            if not name_matches(filename, args.name_contains, args.ignore_case):
                if args.verbose:
                    logger.debug(f"SKIP nombre: {filename}")
                if len(sample_skipped_name) < 10:
                    sample_skipped_name.append(filename)
                continue

            matched_name += 1

            flat_name = flat_name_with_hash(remote_path, rel_path)
            key = f"{s3_cfg.prefix.rstrip('/') + '/' if s3_cfg.prefix else ''}{flat_name}"

            if key in done_keys:
                skipped_state += 1
                continue

            # Exists en S3
            try:
                if object_exists(s3, s3_cfg.bucket, key):
                    skipped_exists += 1
                    if len(sample_skipped_exists) < 10:
                        sample_skipped_exists.append(key)
                    done_keys.add(key)
                    state["done_keys"] = sorted(done_keys)
                    save_state(args.state_file, state)
                    continue
            except (EndpointConnectionError, ConnectionClosedError, ClientError) as e:
                logger.warning(f"Problema consultando S3 (head_object): {e}. Continuo; boto/retry puede resolver.")

            logger.info(f"SUBIR: {remote_path} ({size} bytes) -> {key}")

            if args.dry_run:
                uploaded += 1
                if len(sample_uploaded) < 10:
                    sample_uploaded.append(key + "  [dry-run]")
                continue

            # Upload con reconexión si cae SFTP
            try:
                upload_streaming_sftp_to_s3(sftp, s3, s3_cfg, remote_path, key, logger, attempts=5)
            except (EOFError, OSError, IOError, paramiko.SSHException) as e:
                logger.warning(f"Falla SFTP durante upload: {e}")
                reconnect()
                try:
                    upload_streaming_sftp_to_s3(sftp, s3, s3_cfg, remote_path, key, logger, attempts=5)
                except Exception as e2:
                    failed_uploads += 1
                    if len(sample_failed) < 10:
                        sample_failed.append(f"{remote_path} -> {key} | {e2}")
                    logger.error(f"Fallo definitivo subiendo {remote_path}: {e2}")
                    continue
            except Exception as e:
                failed_uploads += 1
                if len(sample_failed) < 10:
                    sample_failed.append(f"{remote_path} -> {key} | {e}")
                logger.error(f"Fallo subiendo {remote_path}: {e}")
                continue

            # Verificación por tamaño
            if not verify_uploaded_size(s3, s3_cfg.bucket, key, size):
                failed_uploads += 1
                if len(sample_failed) < 10:
                    sample_failed.append(f"{remote_path} -> {key} | verify(ContentLength) FAILED (expected {size})")
                logger.error(f"Upload no verificado por tamaño (ContentLength != {size}). Queda para reintento.")
                continue

            uploaded += 1
            if len(sample_uploaded) < 10:
                sample_uploaded.append(key)

            # state
            done_keys.add(key)
            state["done_keys"] = sorted(done_keys)
            save_state(args.state_file, state)

            if args.max_files and uploaded >= args.max_files:
                logger.info(f"Alcanzado --max-files={args.max_files}. Corto ejecución.")
                break

    finally:
        safe_close(sftp)
        safe_close(ssh)

        end_ts = datetime.now(tz=tz)
        elapsed_s = (end_ts - start_ts).total_seconds()

        # Generar reporte de texto (siempre)
        report_lines = []
        report_lines.append("=" * 78)
        report_lines.append("REPORTE EJECUCIÓN - SFTPGo -> MinIO (flat + inbound + folderdate)")
        report_lines.append("=" * 78)
        report_lines.append(f"Fecha/Hora inicio: {start_ts.isoformat()}")
        report_lines.append(f"Fecha/Hora fin   : {end_ts.isoformat()}")
        report_lines.append(f"Duración         : {elapsed_s:.1f} segundos")
        report_lines.append("")
        report_lines.append("CONFIG / CRITERIOS")
        report_lines.append(f"- TZ: {args.tz}")
        report_lines.append(f"- Carpeta 'ayer' esperada: {sftp_cfg.remote_root.rstrip('/')}/{yesterday_str}")
        report_lines.append(f"- Carpeta usada: {chosen_folder if chosen_folder else '(no determinada)'}")
        report_lines.append(f"- Modo selección: {chosen_mode if chosen_mode else '(n/a)'}")
        report_lines.append(f"- Fallback latest: {args.fallback_latest}")
        report_lines.append(f"- Filtro nombre contiene: '{args.name_contains}' (ignore_case={args.ignore_case})")
        report_lines.append(f"- Dry-run: {args.dry_run}")
        report_lines.append(f"- S3 bucket: {s3_cfg.bucket}")
        report_lines.append(f"- S3 prefix: '{s3_cfg.prefix}'")
        report_lines.append("")
        report_lines.append("RESULTADOS")
        report_lines.append(f"- Archivos vistos en carpeta: {total_files_seen}")
        report_lines.append(f"- Match por nombre: {matched_name}")
        report_lines.append(f"- Subidos (o simulados en dry-run): {uploaded}")
        report_lines.append(f"- Omitidos (existían en S3): {skipped_exists}")
        report_lines.append(f"- Omitidos (state): {skipped_state}")
        report_lines.append(f"- Fallos (upload/verificación): {failed_uploads}")
        report_lines.append("")
        if sample_uploaded:
            report_lines.append("EJEMPLOS SUBIDOS (top 10)")
            for x in sample_uploaded:
                report_lines.append(f"  - {x}")
            report_lines.append("")
        if sample_skipped_exists:
            report_lines.append("EJEMPLOS OMITIDOS POR EXISTIR (top 10)")
            for x in sample_skipped_exists:
                report_lines.append(f"  - {x}")
            report_lines.append("")
        if sample_skipped_name:
            report_lines.append("EJEMPLOS OMITIDOS POR NOMBRE (top 10)")
            for x in sample_skipped_name:
                report_lines.append(f"  - {x}")
            report_lines.append("")
        if sample_failed:
            report_lines.append("EJEMPLOS FALLIDOS (top 10)")
            for x in sample_failed:
                report_lines.append(f"  - {x}")
            report_lines.append("")

        try:
            write_report(report_file, args.report_append, report_lines)
        except Exception as e:
            # si falla el reporte, al menos lo avisamos por stdout
            print(f"[WARN] No pude escribir el reporte '{report_file}': {e}", file=sys.stderr)

        # también dejamos un log final visible
        print(f"[INFO] Reporte escrito en: {report_file}")


if __name__ == "__main__":
    main()
