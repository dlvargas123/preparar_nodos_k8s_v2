import subprocess
import os
from datetime import datetime

# Configuración de búsqueda
FORBIDDEN_FLAG = "--insecure-port"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
NETSTAT_COMMAND = "ss -tpln | grep kube-apiserver"
REPORT_FILE = f"evidencia_insecure_port_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Insecure Port en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Configurar --insecure-port=0 (Port Hardening)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar proceso activo para detectar el flag (Punto 4)
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if FORBIDDEN_FLAG in process_output:
                status = "REVISAR: El flag --insecure-port está presente. Debe ser 0 o eliminarse."
            else:
                status = "PASÓ: El parámetro --insecure-port NO está presente (Deshabilitado por defecto en v1.32)."

            # 2. Verificar sockets activos (Verificación real de red)
            net_result = subprocess.run(NETSTAT_COMMAND, shell=True, capture_output=True, text=True)
            
            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip() if process_output else 'API Server no detectado'}\n\n")
            report.write(f"PUERTOS EN ESCUCHA (ss):\n{net_result.stdout.strip() if net_result.stdout else 'No se detectaron puertos adicionales'}\n\n")

            # 3. Salud del API Server (Punto 4)
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS DEL SISTEMA:\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "kubectl no disponible.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Aviso: Se requiere root para inspeccionar sockets de red y argumentos de procesos.")
    run_validation()
