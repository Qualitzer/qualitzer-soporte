from pathlib import Path
from copy import deepcopy
import yaml

root = Path(__file__).resolve().parent.parent
workflows = root / ".github/workflows"

def require(condition, message):
    if not condition:
        raise SystemExit("ERROR: " + message)

source = yaml.safe_load(
    (workflows / "flutter-build.yml").read_text(encoding="utf-8")
)
bridge_source = yaml.safe_load(
    (workflows / "bridge.yml").read_text(encoding="utf-8")
)

require(str(source["env"]["VERSION"]) == "1.4.9",
        "El flujo original no corresponde a 1.4.9.")

env = {
    key: value
    for key, value in source["env"].items()
    if "secrets." not in str(value)
}
env.update({
    "UPLOAD_ARTIFACT": "true",
    "TAG_NAME": "qualitzer-test",
    "SIGN_BASE_URL": "-2",
})

# Generacion del puente Rust/Flutter, solo para Windows x64.
bridge = deepcopy(bridge_source["jobs"]["generate_bridge"])
bridge["env"] = deepcopy(bridge_source.get("env", {}))
bridge["timeout-minutes"] = 90

matrix = bridge["strategy"]["matrix"]["job"]
bridge["strategy"]["matrix"]["job"] = [
    entry for entry in matrix
    if entry.get("artifact-name") == "bridge-artifact"
]
require(len(bridge["strategy"]["matrix"]["job"]) == 1,
        "No se encontro la configuracion del puente x64.")

# Evitar el servicio de cache antiguo de Actions.
for step in bridge["steps"]:
    if str(step.get("uses", "")).startswith("actions/cache@"):
        step["uses"] = "actions/cache@v4"

# Dependencia nativa original, solo x64.
topmost = deepcopy(
    source["jobs"]["build-RustDeskTempTopMostWindow"]
)
topmost["strategy"]["matrix"]["job"] = [
    entry for entry in topmost["strategy"]["matrix"]["job"]
    if entry.get("platform") == "x64"
]
require(len(topmost["strategy"]["matrix"]["job"]) == 1,
        "No se encontro la dependencia nativa x64.")
topmost["with"]["upload-artifact"] = True

# Compilacion Windows original, restringida a x64.
windows = deepcopy(source["jobs"]["build-for-windows-flutter"])
windows["name"] = "Qualitzer Soporte - Windows x64"
windows["timeout-minutes"] = 180
windows["strategy"]["matrix"]["job"] = [
    entry for entry in windows["strategy"]["matrix"]["job"]
    if entry.get("arch") == "x86_64"
]
require(len(windows["strategy"]["matrix"]["job"]) == 1,
        "No se encontro la compilacion Windows x64.")

steps = []
patched = False
packed = False

for step in windows["steps"]:
    name = step.get("name", "")

    # Sin firma externa ni subida del paquete intermedio.
    if name in ("Sign rustdesk files", "Upload unsigned"):
        continue

    if name == "Build rustdesk":
        steps.append({
            "name": "Aplicar personalizacion Qualitzer",
            "shell": "bash",
            "run": (
                "set -euo pipefail\n"
                "git -C libs/hbb_common apply --check "
                "../../branding/hbb-common.patch\n"
                "git -C libs/hbb_common apply "
                "../../branding/hbb-common.patch\n"
                "cp branding/qualitzer-icon.ico res/icon.ico\n"
                "cp branding/qualitzer-icon.ico "
                "flutter/windows/runner/resources/app_icon.ico\n"
                "cp branding/qualitzer-icon.ico flutter/assets/icon.ico\n"
                "cp branding/qualitzer-icon.png flutter/assets/logo.png\n"
            ),
        })
        patched = True

    steps.append(step)

    # Terminar despues del empaquetado: no MSI, firma ni Release.
    if name == "Build self-extracted executable":
        packed = True
        break

require(patched and packed,
        "La estructura original cambio; no se genero el flujo.")

steps.append({
    "name": "Verificar recursos y preparar descarga",
    "shell": "pwsh",
    "run": r"""
$ErrorActionPreference = 'Stop'
$exe = './SignOutput/rustdesk-1.4.9-x86_64.exe'
if (!(Test-Path $exe)) { throw 'No se genero el ejecutable' }
$assets = './rustdesk/data/flutter_assets/assets'
foreach ($pair in @(
  @('./branding/qualitzer-icon.png', "$assets/logo.png"),
  @('./branding/qualitzer-icon.ico', "$assets/icon.ico")
)) {
  if (!(Test-Path $pair[1])) { throw "Falta recurso: $($pair[1])" }
  if ((Get-FileHash $pair[0]).Hash -ne (Get-FileHash $pair[1]).Hash) {
    throw "Recurso de marca diferente: $($pair[1])"
  }
}
New-Item -ItemType Directory -Force ./qualitzer-dist | Out-Null
Copy-Item $exe ./qualitzer-dist/Qualitzer-Soporte-1.4.9-windows-x64.exe
$hash = (Get-FileHash ./qualitzer-dist/Qualitzer-Soporte-1.4.9-windows-x64.exe -Algorithm SHA256).Hash.ToLower()
"$hash  Qualitzer-Soporte-1.4.9-windows-x64.exe" |
  Set-Content ./qualitzer-dist/SHA256SUMS.txt -Encoding ascii
""".strip(),
})
steps.append({
    "name": "Guardar ejecutable de prueba",
    "uses": "actions/upload-artifact@v4",
    "with": {
        "name": "Qualitzer-Soporte-Windows-x64",
        "path": "qualitzer-dist/",
        "if-no-files-found": "error",
        "retention-days": 7,
    },
})
windows["steps"] = steps

document = {
    "name": "Qualitzer - Windows x64",
    "on": {"workflow_dispatch": {}},
    "permissions": {"contents": "read"},
    "env": env,
    "jobs": {
        "generate-bridge": bridge,
        "build-RustDeskTempTopMostWindow": topmost,
        "build-for-windows-flutter": windows,
    },
}

output = workflows / "qualitzer-windows.yml"
rendered = yaml.safe_dump(document, sort_keys=False, width=120)
require("secrets." not in rendered,
        "Quedo una referencia inesperada a secretos.")
require("action-gh-release@" not in rendered,
        "Quedo una publicacion automatica.")
output.write_text(rendered, encoding="utf-8")

print("FLUJO CREADO: Qualitzer - Windows x64")
print("Activacion: manual")
print("Destino: Windows x64")
print("Salida: ejecutable de prueba sin firma + SHA256")
print("Publicacion automatica: NO")
