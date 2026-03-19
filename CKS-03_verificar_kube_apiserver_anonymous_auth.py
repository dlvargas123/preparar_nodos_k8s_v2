import subprocess

# Verificar si kube-apiserver está ejecutándose con --anonymous-auth=false
def check_anonymous_auth():
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, check=True)
        if "--anonymous-auth=false" in result.stdout:
            print("El kube-apiserver está ejecutándose con --anonymous-auth=false.")
        else:
            print("El kube-apiserver NO está ejecutándose con --anonymous-auth=false.")
    except subprocess.CalledProcessError:
        print("Error al verificar el proceso kube-apiserver.")

# Verificar estado del pod kube-apiserver
def check_kube_apiserver_pod():
    try:
        result = subprocess.run(["kubectl", "-n", "kube-system", "get", "pods", "-o", "wide"], capture_output=True, text=True, check=True)
        if "kube-apiserver" in result.stdout:
            print("El pod kube-apiserver está corriendo.")
        else:
            print("El pod kube-apiserver NO está corriendo.")
    except subprocess.CalledProcessError:
        print("Error al verificar el pod kube-apiserver.")

# Generar reporte de evidencia
def generate_report():
    with open("/tmp/reporte_kube_apiserver_anonymous_auth.txt", "w") as report:
        report.write("Reporte de verificación de --anonymous-auth en kube-apiserver:\n\n")
        report.write("1. Verificación del proceso kube-apiserver:\n")
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        if "--anonymous-auth=false" in result.stdout:
            report.write("El kube-apiserver está ejecutándose con --anonymous-auth=false.\n")
        else:
            report.write("El kube-apiserver NO está ejecutándose con --anonymous-auth=false.\n")
        
        report.write("\n2. Verificación del pod kube-apiserver:\n")
        result = subprocess.run(["kubectl", "-n", "kube-system", "get", "pods", "-o", "wide"], capture_output=True, text=True)
        if "kube-apiserver" in result.stdout:
            report.write("El pod kube-apiserver está corriendo.\n")
        else:
            report.write("El pod kube-apiserver NO está corriendo.\n")

    print("Reporte generado exitosamente en /tmp/reporte_kube_apiserver_anonymous_auth.txt")

# Ejecutar verificaciones y generar reporte
check_anonymous_auth()
check_kube_apiserver_pod()
generate_report()
