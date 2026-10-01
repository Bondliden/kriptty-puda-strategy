$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$tmp = "$env:TEMP\puda_hist"
$out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'historico_puda_104'
New-Item -ItemType Directory -Force $tmp, $out | Out-Null
$b = 'https://data.binance.vision/data'
$monedas = ('BTC ETH BNB SOL XRP DOGE ADA TRX AVAX LINK DOT TON 1000SHIB LTC BCH NEAR UNI APT ICP ETC ' +
  'XLM FIL ATOM ARB OP SUI INJ AAVE HBAR VET MKR GRT ALGO SAND MANA AXS EGLD THETA FTM EOS ' +
  'XTZ FLOW CHZ KAVA NEO ZEC DASH XMR IOTA CRV SNX COMP 1INCH SUSHI YFI ENJ BAT ZIL QTUM ONT ' +
  'ICX WAVES KSM RUNE LDO STX IMX GALA APE GMT DYDX 1000PEPE WLD TIA SEI JUP PYTH ENA ORDI BLUR ' +
  'FET CFX MINA ROSE ONE CELO ANKR LRC STORJ 1000FLOKI 1000BONK WIF JASMY HOT ZRX ENS MASK AR ' +
  'CAKE TRB MATIC LUNA FTT SRM') -split ' '
$conSpot = ('BTC ETH BNB SOL XRP DOGE ADA TRX AVAX LINK DOT LTC BCH NEAR UNI ATOM ETC FIL XLM AAVE ' +
  'ARB OP SUI INJ APT MATIC FTM SAND MANA AXS') -split ' '
$meses = foreach ($y in 2017..2026) { foreach ($m in 1..12) { '{0}-{1:D2}' -f $y, $m } }
$meses = @($meses | Where-Object { $_ -ge '2017-08' -and $_ -le '2026-09' })
[array]::Reverse($meses)

function Bajar($url, $file) {
  if (Test-Path $file) { return $true }
  try { Invoke-WebRequest $url -OutFile $file -UseBasicParsing -TimeoutSec 60; return $true } catch { return $false }
}

$todas = @($monedas + ($conSpot | Where-Object { $monedas -notcontains $_ }))
$n = 0
foreach ($a in $todas) {
  $n++; Write-Host "[$n/$($todas.Count)] $a"
  $s = "${a}USDT"; $d = "$tmp\$a"
  New-Item -ItemType Directory -Force $d | Out-Null
  if ($monedas -contains $a) {
    $hay = $false; $fallos = 0
    foreach ($mm in $meses) {
      if ($mm -lt '2019-09') { break }
      if (Bajar "$b/futures/um/monthly/klines/$s/1h/$s-1h-$mm.zip" "$d\perp-$mm.zip") {
        $hay = $true; $fallos = 0
        [void](Bajar "$b/futures/um/monthly/fundingRate/$s/$s-fundingRate-$mm.zip" "$d\funding-$mm.zip")
      } elseif ($hay) { $fallos++; if ($fallos -ge 3) { break } }
    }
  }
  if ($conSpot -contains $a) {
    $hay = $false; $fallos = 0
    foreach ($mm in $meses) {
      if (Bajar "$b/spot/monthly/klines/$s/1h/$s-1h-$mm.zip" "$d\spot-$mm.zip") { $hay = $true; $fallos = 0 }
      elseif ($hay) { $fallos++; if ($fallos -ge 3) { break } }
    }
  }
}

Remove-Item "$out\*.zip" -ErrorAction SilentlyContinue
$dirs = @(Get-ChildItem $tmp -Directory | Where-Object { Get-ChildItem $_.FullName -File })
for ($i = 0; $i -lt $dirs.Count; $i += 2) {
  $g = $dirs[$i..([Math]::Min($i + 1, $dirs.Count - 1))]
  Compress-Archive -Path $g.FullName -DestinationPath ("$out\lote{0:D2}.zip" -f ($i / 2 + 1)) -Force
}
Write-Host "LISTO: $($dirs.Count) monedas en $out"
explorer $out
