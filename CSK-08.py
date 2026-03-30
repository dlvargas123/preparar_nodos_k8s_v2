import subprocess
import os
from datetime import datetime

# Flags críticos para la seguridad de etcd
ETCD_SECURITY_FLAGS = [
    "--client-cert-auth=true",
    "--cert-file",
    "--key-file",
    "--trusted-ca-file",
    "--peer-client-cert-auth=true"
]

CHECK_COMMAND = "ps -ef | grep 'rke2' | grep 'etcd' | grep -v grep"
REPORT_FILE = f"evidencia_etcd_tls_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de mTLS en etcd (RKE2)...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD ETCD - {datetime.now()}\n")
        report.write(f"CONTROL: Validar --client-cert-auth=true y Configuración TLS\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Obtener la línea de comandos del proceso etcd
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if not process_output:
                status = "ERROR: No se detectó el proceso de etcd. ¿RKE2 está corriendo?"
                report.write(status + "\n")
                print(status)
                return

            report.write("--- 1. VERIFICACIÓN DE FLAGS DE SEGURIDAD ---\n")
            all_passed = True
            for flag in ETCD_SECURITY_FLAGS:
                if flag in process_output:
                    res = f"PRESENTE: {flag}"
                    report.write(f"[OK] {res}\n")
                else:
                    res = f"FALTANTE: {flag}"
                    report.write(f"[FALLO] {res}\n")
                    all_passed = False
            
            final_status = "PASÓ" if all_passed else "REVISAR"
            report.write(f"\nESTADO FINAL: {final_status}\n\n")

            # 2. Evidencia del proceso
            report.write("--- 2. EVIDENCIA DEL RUNTIME (ps) ---\n")
            report.write(process_output.strip() + "\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA EJECUCIÓN: {e}\n")

    print(f"\nValidación finalizada. Reporte de evidencia: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Se requiere root para inspeccionar los procesos del sistema.")
    else:
        run_validation()
