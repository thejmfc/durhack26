# Downloads the free NASA source data used by Skyline (~750 MB total, including global high-resolution terrain) into data/raw/.
# File names verified against the source pages on 2026-11-14:
#   https://svs.gsfc.nasa.gov/4720            (CGI Moon Kit - visuals only, not science-grade)
#   https://pgda.gsfc.nasa.gov/products/78    (LOLA 5 m/px south-pole site DEMs)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

if (-not (Test-Path 'data/raw')) {
    New-Item -Path 'data/raw' -ItemType Directory | Out-Null
}

$SVS = 'https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720'
$PGDA = 'https://pgda.gsfc.nasa.gov/data/LOLA_5mpp'

function Fetch-File {
    param([string]$Url)
    $filename = Split-Path -Leaf $Url
    $outPath = "data/raw/$filename"
    $partPath = "$outPath.part"
    
    if ((Test-Path $outPath) -and (Get-Item $outPath).Length -gt 0) {
        Write-Host "have $outPath"
    } else {
        Write-Host "get  $Url"
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Url -OutFile $partPath -ErrorAction Stop
        Move-Item -Path $partPath -Destination $outPath -Force
    }
}

# Global colour map (4k, poles filled)
Fetch-File "$SVS/lroc_color_poles_4k.tif"

# Global detail: 64 px/deg (~474 m at equator)
Fetch-File "$SVS/ldem_64_uint.tif"

# Global displacement, 16 px/deg, uint16 half-metres
Fetch-File "$SVS/ldem_16_uint.tif"

# 5 m DEMs, 16 x 16 km each: Connecting ridge, Shackleton rim, Peak near Shackleton, de Gerlache rim, Leibnitz beta plateau
@('Site01', 'Site04', 'Site07', 'Site11', 'Site20') | ForEach-Object {
    Fetch-File "$PGDA/$_/${_}_final_adj_5mpp_surf.tif"
}

# README
Fetch-File "$PGDA/README"

Write-Host "`nData files downloaded:"
Get-ChildItem -Path 'data/raw' -File | Format-Table -Property Name, @{Name='Size';Expression={'{0:N0} KB' -f ($_.Length/1KB)}}
