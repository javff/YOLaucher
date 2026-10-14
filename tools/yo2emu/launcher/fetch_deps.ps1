# fetch_deps.ps1 - downloads the two third-party binaries that the launcher embeds at build time (go:embed):
#   juego\frida-inject.exe   Frida 17.15.5 (github.com/frida/frida releases), fallback game hook
#   sala\tor.exe             Tor Expert Bundle 15.0.24 for Windows x86_64 (torproject.org), fallback transport
# Only official URLs, and every file is checked against a pinned SHA-256 before it is used. Nothing downloaded is ever
# executed by this script. PowerShell 5.1 compatible; ASCII on purpose.
#
#   powershell -ExecutionPolicy Bypass -File fetch_deps.ps1            # both (skips the ones already present and valid)
#   powershell -ExecutionPolicy Bypass -File fetch_deps.ps1 -Only tor  # or -Only frida
#   powershell -ExecutionPolicy Bypass -File fetch_deps.ps1 -Force     # download again even if present
#
# Needs: Python 3 on the PATH (to decompress Frida's .xz) and tar.exe (built into Windows 10 1803+).
# Downloads are kept in _descargas\ (git-ignored). To use other versions, change the constants AND the hashes below.

param([ValidateSet("", "frida", "tor")][string]$Only = "", [switch]$Force)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$FridaVer = "17.15.5"
$FridaUrl = "https://github.com/frida/frida/releases/download/$FridaVer/frida-inject-$FridaVer-windows-x86_64.exe.xz"
$FridaExeSha = "a68292406ae48509f297dc2d98cb30a7828182d31de32cf851a0a31ae2160989"   # frida-inject.exe after unpacking

$TorVer = "15.0.24"
$TorTgz = "tor-expert-bundle-windows-x86_64-$TorVer.tar.gz"
$TorUrls = @("https://dist.torproject.org/torbrowser/$TorVer/$TorTgz",
             "https://archive.torproject.org/tor-package-archive/torbrowser/$TorVer/$TorTgz")
$TorTgzSha = "e9dc6ccc93cd6afa507193f4de284d6424233ff5102155cd2c94b259e8a22b65"   # = sha256sums-signed-build.txt
$TorExeSha = "90bbdcafd586feea608a5e9b7d3959f4ee194f7770755cfde8fab240e9773ad1"

$dl = Join-Path $here "_descargas"
New-Item -ItemType Directory -Force $dl | Out-Null

function Sha256($path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }

function Valid($path, $sha) { (Test-Path -LiteralPath $path) -and ((Sha256 $path) -eq $sha) }

function Download($urls, $dest) {
    foreach ($u in $urls) {
        try {
            Write-Host "Downloading $u"
            Invoke-WebRequest -Uri $u -OutFile $dest -UseBasicParsing
            return
        } catch {
            Write-Host "  failed: $($_.Exception.Message)"
        }
    }
    throw "could not download $(Split-Path -Leaf $dest)"
}

function Get-Frida {
    $exe = Join-Path $here "juego\frida-inject.exe"
    if (-not $Force -and (Valid $exe $FridaExeSha)) { Write-Host "frida-inject.exe already present (sha256 ok)"; return }
    $xz = Join-Path $dl (Split-Path -Leaf $FridaUrl)
    Download @($FridaUrl) $xz
    $tmp = "$exe.tmp"
    & python -c "import lzma,shutil,sys; shutil.copyfileobj(lzma.open(sys.argv[1]), open(sys.argv[2],'wb'))" $xz $tmp
    if ($LASTEXITCODE -ne 0) { throw "could not decompress $xz (is Python on the PATH?)" }
    $got = Sha256 $tmp
    if ($got -ne $FridaExeSha) { Remove-Item $tmp; throw "frida-inject.exe: sha256 $got, expected $FridaExeSha" }
    Move-Item -Force $tmp $exe
    Write-Host "OK juego\frida-inject.exe ($FridaVer)"
}

function Get-Tor {
    $exe = Join-Path $here "sala\tor.exe"
    if (-not $Force -and (Valid $exe $TorExeSha)) { Write-Host "tor.exe already present (sha256 ok)"; return }
    $tgz = Join-Path $dl $TorTgz
    if ($Force -or -not (Valid $tgz $TorTgzSha)) { Download $TorUrls $tgz }
    $got = Sha256 $tgz
    if ($got -ne $TorTgzSha) { throw "$TorTgz : sha256 $got, expected $TorTgzSha" }
    $out = Join-Path $dl "tor-$TorVer"
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
    New-Item -ItemType Directory -Force $out | Out-Null
    & tar.exe -xzf $tgz -C $out "tor/tor.exe"
    if ($LASTEXITCODE -ne 0) { throw "tar could not extract tor/tor.exe from $TorTgz" }
    $t = Join-Path $out "tor\tor.exe"
    $got = Sha256 $t
    if ($got -ne $TorExeSha) { throw "tor.exe: sha256 $got, expected $TorExeSha" }
    Copy-Item -Force $t $exe
    Write-Host "OK sala\tor.exe (Tor Expert Bundle $TorVer)"
}

if ($Only -eq "" -or $Only -eq "frida") { Get-Frida }
if ($Only -eq "" -or $Only -eq "tor") { Get-Tor }
Write-Host "Done. Next: app\build.ps1"
