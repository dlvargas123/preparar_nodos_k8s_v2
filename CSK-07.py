import subprocess
import os
from datetime import datetime

# Flags críticos a validar para etcd
ETCD_FLAGS = ["--etcd-cafile", "--etcd-certfile", "--etcd-keyfile", "--etcd-servers"]
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_etcd_flags_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de flags --etcd-* en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Comunicación Segura API Server <-> etcd\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Obtener proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout
            
            report.write("--- 1. VERIFICACIÓN DE FLAGS EN RUNTIME ---\n")
            all_passed = True
            for flag in ETCD_FLAGS:
                if flag in process_output:
                    # Extraer ruta para validar existencia
                    path = process_output.split(flag + "=")[1].split(" ")[0].strip()
                    exists = os.path.exists(path)
                    res = f"PRESENTE: {flag} -> {path} (Existe: {exists})"
                    if not exists: all_passed = False
                else:
                    res = f"FALTANTE: {flag}"
                    all_passed = False
                report.write(res + "\n")
            
            status = "PASÓ" if all_passed else "REVISAR"
            print(f"Estado de la validación: {status}")
            report.write(f"\nESTADO FINAL: {status}\n\n")

            # 2. Evidencia cruda del proceso
            report.write("--- 2. EVIDENCIA DEL PROCESO (ps) ---\n")
            report.write(process_output.strip() + "\n\n")

            # 3. Verificación de salud de etcd vía rke2
            report.write("--- 3. SALUD DE ETCD (KUBE-SYSTEM) ---\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=etcd"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "Pod de etcd no detectado.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"Reporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere root para inspeccionar certificados y procesos.")
    else:
        run_validation()
