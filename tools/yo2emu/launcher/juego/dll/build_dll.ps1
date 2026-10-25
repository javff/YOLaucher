# Compila yo2net.c -> ..\dllbin\yo2net-version.dll (DLL de 32 bits; el lanzador la copia como version.dll junto a yo2.exe).
# PowerShell 5.1, fichero en ASCII a proposito. Despues hay que recompilar el lanzador (app\build.ps1): la incrusta.
#
#   powershell -ExecutionPolicy Bypass -File juego\dll\build_dll.ps1
#   powershell -ExecutionPolicy Bypass -File juego\dll\build_dll.ps1 -Compilador C:\ruta\i686-w64-mingw32-gcc.exe
#
# Donde se busca el compilador, en este orden:
#   a. -Compilador <exe>                      (i686-w64-mingw32-gcc.exe, ...-clang.exe, zig.exe o cl.exe)
#   b. variable de entorno YO2_CC=<exe>       (lo mismo, para no repetirlo en cada llamada)
#   c. variable de entorno W64DEVKIT=<carpeta de w64devkit x86> (se usa su bin\i686-w64-mingw32-gcc.exe)
#   d. el PATH (nombres de abajo)
#   e. default: w64devkit x86 en %USERPROFILE%\tools\w64devkit o en C:\w64devkit (donde lo deja el
#      descomprimido de https://github.com/skeeto/w64devkit/releases, version "x86")
# Vale cualquiera de estos:
#   1. MinGW-w64 de 32 bits: i686-w64-mingw32-gcc.exe o i686-w64-mingw32-clang.exe (w64devkit x86, llvm-mingw, MSYS2)
#   2. zig.exe (zig cc -target x86-windows-gnu)
#   3. cl.exe de MSVC para x86 (abrir "x86 Native Tools Command Prompt" de Visual Studio Build Tools antes)
# Sin CRT: la DLL solo importa kernel32.dll, user32.dll y gdi32.dll (las dos ultimas, para la ventana ampliada: yo2.exe ya
# las carga). Al acabar se comprueba con python + pefile (si estan): PE de 32 bits, las 17 exportaciones de version.dll,
# solo KERNEL32/USER32/GDI32 importado y la marca "YO2ONLINE-NETDLL" dentro. Tambien pasa las pruebas de las cuentas
# (test_ventana.c) con el gcc normal de la carpeta del compilador, si lo hay.
param([string]$Compilador = "")
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path        # ...\launcher\juego\dll
$src = Join-Path $here "yo2net.c"
$def = Join-Path $here "version.def"
$outDir = Join-Path (Split-Path -Parent $here) "dllbin"
$out = Join-Path $outDir "yo2net-version.dll"
New-Item -ItemType Directory -Force $outDir | Out-Null
$tmp = Join-Path $env:TEMP ("yo2net-" + [guid]::NewGuid().ToString("N") + ".dll")

function Buscar($nombres) {
    foreach ($n in $nombres) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

$cc = $Compilador
if (-not $cc -and $env:YO2_CC) { $cc = $env:YO2_CC }
if (-not $cc -and $env:W64DEVKIT) {
    $c = Join-Path $env:W64DEVKIT "bin\i686-w64-mingw32-gcc.exe"
    if (Test-Path $c) { $cc = $c }
}
if (-not $cc) { $cc = Buscar @("i686-w64-mingw32-gcc", "i686-w64-mingw32-clang", "zig", "cl") }
if (-not $cc) {
    foreach ($d in @((Join-Path $env:USERPROFILE "tools\w64devkit"), "C:\w64devkit")) {
        $c = Join-Path $d "bin\i686-w64-mingw32-gcc.exe"
        if (Test-Path $c) { $cc = $c; break }
    }
}
if ($cc -and -not (Test-Path $cc)) {   # -Compilador/YO2_CC pueden ser un nombre del PATH (zig, cl...)
    $c = Buscar @($cc)
    if (-not $c) { Write-Host "No existe el compilador indicado: $cc"; exit 1 }
    $cc = $c
}
if (-not $cc) {
    Write-Host "No hay compilador de C de 32 bits (-Compilador, YO2_CC, W64DEVKIT o PATH). Vale uno de estos:"
    Write-Host "  - w64devkit (x86), llvm-mingw (i686-w64-mingw32-clang), MSYS2 mingw-w64-i686-gcc, zig, o MSVC x86"
    exit 1
}
$nombre = [IO.Path]::GetFileNameWithoutExtension($cc).ToLower()
Write-Host "Compilador: $cc"
$env:Path = (Split-Path $cc) + ";" + $env:Path   # gcc de w64devkit busca as/ld junto a el

$gnu = @("-O2", "-s", "-shared", "-nostdlib", "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
         "-fno-asynchronous-unwind-tables", "-Wall", "-Wno-unused-function",
         "-Wl,--entry,_DllMain@12", "-Wl,--image-base,0x6a5d0000", "-Wl,--dynamicbase", "-Wl,--nxcompat",
         $src, $def, "-lkernel32", "-luser32", "-lgdi32", "-o", $tmp)
if ($nombre -eq "zig") {
    & $cc cc -target x86-windows-gnu @gnu
} elseif ($nombre -eq "cl") {
    $obj = Join-Path $env:TEMP "yo2net.obj"
    & $cc /nologo /O1 /GS- /W3 /c $src /Fo$obj
    if ($LASTEXITCODE -eq 0) {
        $link = Join-Path (Split-Path $cc) "link.exe"
        & $link /nologo /DLL /MACHINE:X86 /NODEFAULTLIB /ENTRY:DllMain /DEF:$def /BASE:0x6a5d0000 /DYNAMICBASE /NXCOMPAT `
            /OUT:$tmp $obj kernel32.lib user32.lib gdi32.lib
    }
} else {
    & $cc @gnu
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmp)) { Write-Host "La compilacion fallo."; exit 1 }

# ---- comprobar lo que ha salido antes de dejarlo donde se incrusta
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $chk = @'
import sys
try:
    import pefile
except ImportError:
    print("pefile no esta: sin comprobar"); sys.exit(0)
pe = pefile.PE(sys.argv[1])
assert pe.FILE_HEADER.Machine == 0x14c, "no es de 32 bits"
ex = sorted((s.name or b"").decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols)
assert len(ex) == 17 and "GetFileVersionInfoA" in ex and "VerQueryValueA" in ex, ex
imp = [e.dll.decode().upper() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
assert imp and set(imp) <= {"KERNEL32.DLL", "USER32.DLL", "GDI32.DLL"} and "KERNEL32.DLL" in imp, imp
assert b"YO2ONLINE-NETDLL" in open(sys.argv[1], "rb").read(), "falta la marca"
print("OK: PE32, 17 exportaciones, importa solo", imp, "marca presente")
'@
    $chkf = Join-Path $env:TEMP "yo2net-chk.py"
    Set-Content -Path $chkf -Value $chk -Encoding ASCII
    & $py.Source -I $chkf $tmp
    $rc = $LASTEXITCODE; Remove-Item -Force $chkf -ErrorAction SilentlyContinue
    if ($rc -ne 0) { Write-Host "La DLL no pasa la comprobacion: no se copia."; exit 1 }
}
Move-Item -Force $tmp $out
Write-Host ("Hecho: $out (" + (Get-Item $out).Length + " bytes). Ahora: app\build.ps1 para incrustarla.")
