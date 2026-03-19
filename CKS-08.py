import subprocess
import os
from datetime import datetime

# Configuración de búsqueda para RKE2
TARGET_FLAG = "--kubelet-certificate-authority"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_kubelet_ca_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Kubelet CA en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Configurar --kubelet-certificate-authority\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar presencia del flag en el proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if TARGET_FLAG in process_output:
                # Extraer la ruta de la CA para la evidencia
                parts = process_output.split(TARGET_FLAG + "=")
                ca_path = parts[1].split(" ")[0].strip() if len(parts) > 1 else "Ruta no detectada"
                
                status = "PASÓ: El flag --kubelet-certificate-authority está configurado."
                detail = f"Ruta de CA detectada: {ca_path}"
            else:
                status = "ADVERTENCIA: El flag no se encuentra explícito en el comando de ejecución."
                detail = "Verificar si RKE2 lo gestiona mediante parámetros internos de seguridad."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"DETALLE: {detail}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip()}\n\n")

            # 2. Verificar existencia física del archivo CA
            if TARGET_FLAG in process_output and os.path.exists(ca_path):
                report.write(f"VALIDACIÓN FÍSICA: El archivo de la CA existe en la ruta de RKE2.\n")
            
            # 3. Salud del API Server
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS (KUBE-SYSTEM):\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "kubectl no disponible en este nodo.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"Reporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Se requiere privilegios de ROOT para inspeccionar los parámetros del API Server.")
    else:
        run_validation()
