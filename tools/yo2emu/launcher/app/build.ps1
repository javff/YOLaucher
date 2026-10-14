# Compila la ventana: ..\dist-app\YO2OnlineLauncher.exe (Wails v2). PowerShell 5.1, fichero en ASCII a proposito.
# Nombre (12-10): YO2OnlineLauncher.exe, NUNCA YO2Launcher.exe: Windows no distingue mayusculas y ese nombre pisaba el
# lanzador de Konami (Yo2Launcher.exe) de la carpeta del cliente. Un dist-app\YO2Launcher.exe de antes no se toca.
#
#   powershell -ExecutionPolicy Bypass -File app\build.ps1            # compila
#   powershell -ExecutionPolicy Bypass -File app\build.ps1 -Firmar    # ademas lo firma como editor (ver abajo)
#
# Necesita:
#   - Go (en el PATH o en %LOCALAPPDATA%\Programs\go\bin\go.exe)
#   - la herramienta wails 2.16.0:  go install github.com/wailsapp/wails/v2/cmd/wails@v2.16.0
#     (queda en %USERPROFILE%\go\bin\wails.exe). La libreria esta fijada en go.mod a la misma version.
#   - juego\frida-inject.exe y sala\tor.exe (los dos van incrustados; estan en .gitignore). Si faltan, se copian del arbol
#     principal del repo (el primero de "git worktree list") o, tor.exe, de _descargas\tor\tor\tor.exe.
#   - Runtime WebView2 de Microsoft en el PC donde se ejecute (viene con Windows 10/11 actualizado).
# No hay frontend con npm: la pagina es HTML/CSS/JS plano en app\frontend\dist.
#
# NO firma por defecto. Para que los anfitriones puedan repartir esta version por autoactualizacion hay que firmarla con
# la clave del editor (-Firmar, o a mano: firmar.exe -version <version> dist-app\YO2OnlineLauncher.exe); sin la firma
# (YO2OnlineLauncher.exe.firma junto al exe) el servidor propio no la reparte.
# -Salida <carpeta>: deja el exe en otra carpeta en vez de dist-app (para probar sin tocar un YO2OnlineLauncher.exe que este
#   abierto y bloqueado en dist-app). Con -Salida no se firma y dist-app no se toca.
# -Prueba (solo con -Salida): compila tambien app\prueba_ctl.go (etiqueta "prueba": control por 127.0.0.1 para las pruebas de
#   extremo a extremo con YO2_PRUEBA_CTL). Nunca para repartir.
param([switch]$Firmar, [string]$Salida = "", [switch]$Prueba)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\launcher\app
$launcher = Split-Path -Parent $here                         # ...\launcher
Set-Location $launcher

# ---- Go y wails
$go = (Get-Command go -ErrorAction SilentlyContinue).Source
if (-not $go) {
    $cand = Join-Path $env:LOCALAPPDATA "Programs\go\bin\go.exe"
    if (Test-Path $cand) { $go = $cand }
}
if (-not $go) { Write-Host "Falta Go. Descargalo de https://go.dev/dl/ y descomprimelo en $env:LOCALAPPDATA\Programs\."; exit 1 }
$wails = (Get-Command wails -ErrorAction SilentlyContinue).Source
if (-not $wails) {
    $cand = Join-Path $env:USERPROFILE "go\bin\wails.exe"
    if (Test-Path $cand) { $wails = $cand }
}
if (-not $wails) { Write-Host "Falta wails. Instalalo con:  go install github.com/wailsapp/wails/v2/cmd/wails@v2.16.0"; exit 1 }
$env:Path = (Split-Path $go) + ";" + $env:Path
Write-Host "Go: $go"; & $go version
Write-Host "wails: $wails"; & $wails version | Select-Object -First 1

# ---- entradas ignoradas por git (se incrustan): si faltan en esta copia, del arbol principal
$principal = $null
try {
    $linea = (& git -C $launcher worktree list --porcelain 2>$null | Select-Object -First 1)
    if ($linea -match "^worktree (.+)$") { $principal = Join-Path $Matches[1] "tools\yo2emu\launcher" }
} catch { }
function Traer($rel, $extras) {
    if (Test-Path $rel) { return }
    $fuentes = @()
    if ($principal) { $fuentes += (Join-Path $principal $rel); if ($rel -eq "juego\frida-inject.exe") { $fuentes += (Join-Path $principal "frida-inject.exe") } }
    $fuentes += $extras
    foreach ($f in $fuentes) {
        if ($f -and (Test-Path $f)) { Write-Host "Copio $rel desde $f"; Copy-Item $f $rel; return }
    }
    Write-Host "Falta $rel (se incrusta en el exe). Mira las notas al principio de este script."; exit 1
}
Traer "juego\frida-inject.exe" @()
Traer "sala\tor.exe" @("_descargas\tor\tor\tor.exe")

# ---- compilar
$ver = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
$env:CGO_ENABLED = "0"; $env:GOOS = "windows"; $env:GOARCH = "amd64"
Set-Location $here
$extra = @()
if ($Prueba) {
    if (-not $Salida) { Write-Host "-Prueba solo vale con -Salida (nunca en dist-app)."; exit 1 }
    $extra = @("-tags", "prueba")
}
& $wails build -s -skipbindings -trimpath @extra -ldflags "-s -w -X main.versionLanzador=$ver" -o YO2OnlineLauncher.exe
if ($LASTEXITCODE -ne 0) { throw "wails build fallo" }
Set-Location $launcher
if ($Salida) {
    New-Item -ItemType Directory -Force $Salida | Out-Null
    Copy-Item "app\build\bin\YO2OnlineLauncher.exe" (Join-Path $Salida "YO2OnlineLauncher.exe") -Force
    $mb = [math]::Round((Get-Item (Join-Path $Salida "YO2OnlineLauncher.exe")).Length / 1MB, 1)
    Write-Host "OK (prueba): $Salida\YO2OnlineLauncher.exe ($mb MB, version $ver). dist-app no se ha tocado."
    exit 0
}
New-Item -ItemType Directory -Force dist-app | Out-Null
if (Test-Path "dist-app\YO2OnlineLauncher.exe.firma") { Remove-Item "dist-app\YO2OnlineLauncher.exe.firma" -Force }
Copy-Item "app\build\bin\YO2OnlineLauncher.exe" "dist-app\YO2OnlineLauncher.exe" -Force

# ---- firma de editor (solo si se pide)
if ($Firmar) {
    & $go build -trimpath -ldflags "-s -w" -o firmar.exe .\firmar
    if ($LASTEXITCODE -ne 0) { throw "go build firmar fallo" }
    & .\firmar.exe -version $ver dist-app\YO2OnlineLauncher.exe
    if ($LASTEXITCODE -ne 0) { throw "firmar fallo" }
} else {
    Write-Host "AVISO: dist-app\YO2OnlineLauncher.exe queda SIN FIRMAR: un servidor propio no lo repartira por autoactualizacion (usa -Firmar)."
}

$mb = [math]::Round((Get-Item dist-app\YO2OnlineLauncher.exe).Length / 1MB, 1)
Write-Host "OK: dist-app\YO2OnlineLauncher.exe ($mb MB, version $ver)."
