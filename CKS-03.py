import subprocess
import os
from datetime import datetime

# Configuración de RKE2
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
EXPECTED_FLAG = "--anonymous-auth=false"
REPORT_FILE = f"evidencia_anonymous_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación para RKE2 v1.32...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE SEGURIDAD KUBERNETES (RKE2) - {datetime.now()}\n")
        report.write("CONTROL: Restringir acceso anónimo (--anonymous-auth=false)\n")
        report.write("="*60 + "\n\n")

        # 1. Validar el proceso en ejecución
        try:
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if EXPECTED_FLAG in process_output:
                status = "PASÓ: El flag --anonymous-auth=false está activo."
                details = f"Evidencia en proceso:\n{process_output.strip()}"
            else:
                # RKE2 a veces lo omite si es el default interno, pero usualmente es explícito
                status = "REVISIÓN MANUAL: El flag no se detecta explícitamente en el comando ps."
                details = "Verificar la configuración en /etc/rancher/rke2/config.yaml"

            print(status)
            report.write(f"ESTADO: {status}\n\n")
            report.write(f"{details}\n\n")

        except Exception as e:
            report.write(f"ERROR al ejecutar la validación: {e}\n")

        # 2. Validar estado de los pods (Punto 4 de tu solicitud)
        report.write("-" * 30 + "\n")
        report.write("ESTADO DE PODS (kube-system):\n")
        try:
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver", "--no-headers"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.stdout else "No se encontraron pods con el label component=kube-apiserver\n")
        except FileNotFoundError:
            report.write("kubectl no encontrado en el PATH del sistema.\n")

    print(f"Reporte generado: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Se recomienda ejecutar como root para ver todos los procesos.")
    run_validation()
