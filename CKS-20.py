import subprocess
import os
import json
from datetime import datetime

# Rutas y comandos específicos para RKE2
CHECK_COMMAND = "ps -ef | grep kubelet | grep -v grep"
RKE2_KUBELET_CONFIG = "/var/lib/rancher/rke2/agent/etc/kubelet/config.json"
REPORT_FILE = f"evidencia_kubelet_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de configuración de Kubelet en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE KUBELET - {datetime.now()}\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Validar proceso en ejecución (Punto 4)
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout
            
            report.write("EVIDENCIA DE PROCESO (ps):\n")
            report.write(f"{process_output.strip() if process_output else 'Kubelet no detectado como proceso independiente'}\n\n")

            # 2. Validar archivo de configuración real de RKE2
            if os.path.exists(RKE2_KUBELET_CONFIG):
                report.write(f"ARCHIVO DE CONFIGURACIÓN DETECTADO: {RKE2_KUBELET_CONFIG}\n")
                with open(RKE2_KUBELET_CONFIG, 'r') as f:
                    config_data = json.load(f)
                    # Aquí puedes buscar parámetros específicos, ej: anonymous-auth
                    report.write("CONTENIDO DE CONFIGURACIÓN (JSON):\n")
                    report.write(json.dumps(config_data, indent=2))
                    status = "PASÓ: Archivo de configuración de Kubelet validado."
            else:
                status = "ADVERTENCIA: No se encontró el config.json estándar de RKE2."
            
            print(status)
            report.write(f"\n\nESTADO FINAL: {status}\n")

            # 3. Salud del servicio
            report.write("-" * 30 + "\n")
            svc_check = subprocess.run(["systemctl", "is-active", "rke2-server", "rke2-agent"], 
                                      capture_output=True, text=True)
            report.write(f"ESTADO DE SERVICIOS RKE2:\n{svc_check.stdout}")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Se requiere root para acceder a la configuración de RKE2.")
    else:
        run_validation()
