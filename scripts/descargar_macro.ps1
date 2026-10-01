$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'historico_macro'
New-Item -ItemType Directory -Force $out | Out-Null
foreach ($id in 'FEDFUNDS', 'CPIAUCSL', 'SP500', 'NASDAQCOM', 'DTWEXBGS', 'VIXCLS', 'T10Y2Y') {
  Write-Host "FRED $id"
  try { Invoke-WebRequest "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$id" -OutFile "$out\$id.csv" -UseBasicParsing -TimeoutSec 120 }
  catch { Write-Host "  fallo: $id" -ForegroundColor Red }
}
Write-Host 'Fear and Greed'
try { Invoke-WebRequest 'https://api.alternative.me/fng/?limit=0&format=csv' -OutFile "$out\fear_greed.csv" -UseBasicParsing -TimeoutSec 120 }
catch { Write-Host '  fallo: fear_greed' -ForegroundColor Red }
Write-Host "LISTO: $out"
explorer $out
