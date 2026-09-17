[CmdletBinding()]
param([ValidateSet('OriginalSdk','LocalApi')][string]$AuthenticationMode='OriginalSdk')
$ErrorActionPreference='Stop'
function Read-KkFreshLog {
 param([string]$Path,[long]$Offset)
 if(-not(Test-Path -LiteralPath $Path)){return ''}
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
 try{
  if($stream.Length -lt $Offset){throw 'Adapter log was truncated during preparation'}
  [void]$stream.Seek($Offset,[IO.SeekOrigin]::Begin)
  $reader=[IO.StreamReader]::new($stream)
  try{return $reader.ReadToEnd()}finally{$reader.Dispose()}
 }finally{$stream.Dispose()}
}
$root='C:\KK-Lab\sdo-original-window-20260913-1931'
$client='C:\KK-Lab\sdo-native-client-20260913-1931'
if(-not(Test-Path -LiteralPath "$root\deployment.json")){throw 'Original-window integration not prepared'}
if(@(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).Count -or @(Get-NetRoute -DestinationPrefix '::/0' -ErrorAction SilentlyContinue).Count){throw 'External route'}
$deployment=Get-Content -LiteralPath "$root\deployment.json" -Raw|ConvertFrom-Json
$ports=@(Get-NetTCPConnection -State Listen -LocalPort 17999,18000,18001,18082 -ErrorAction SilentlyContinue)
if($ports.Count -ne 4 -or @($ports|Where-Object OwningProcess -ne $deployment.service_pid).Count){throw 'Integrated service not ready; do not silently fall back'}
if(@(Get-CimInstance Win32_Process|Where-Object Name -in @('gfxz-lab.exe','Client.exe','sdologin.exe')).Count){throw 'Close existing game first; it will not be stopped automatically'}
$run=Join-Path $root ('entry-'+(Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $run -ErrorAction Stop|Out-Null
if(Test-Path -LiteralPath "$client\kk-roleprop-ready.txt"){Move-Item -LiteralPath "$client\kk-roleprop-ready.txt" -Destination "$run\previous-roleprop-ready.txt"}
$p=Start-Process -FilePath "$client\gfxz-lab.exe" -WorkingDirectory $client -WindowStyle Normal -PassThru
$handle=$p.Handle
@{pid=$p.Id;client_root=$client;started_at=(Get-Date).ToString('o');force_success_callback=$false}|ConvertTo-Json|Set-Content -LiteralPath "$run\launch.json"
$deadline=(Get-Date).AddSeconds(150)
do{
 if($p.HasExited){throw 'Game exited before SDK ready'}
 $sdk=@(Get-CimInstance Win32_Process -Filter "Name='sdologin.exe'"|Where-Object {$_.ParentProcessId -eq $p.Id})
 if($sdk.Count -eq 1){break}
 Start-Sleep -Milliseconds 500
}while((Get-Date)-lt$deadline)
if($sdk.Count -ne 1){throw 'SDK startup timeout'}
# Leave SDK files original; adapt requests only after its update/identity check.
$adapterOffset=if(Test-Path -LiteralPath "$root\adapter.log"){(Get-Item -LiteralPath "$root\adapter.log").Length}else{0}
& 'C:\KK-Lab\kk_inject.exe' "$root\request-adapter.dll" --pid $sdk[0].ProcessId *> "$run\request-adapter-inject.txt"
if($LASTEXITCODE){throw 'Request adapter injection failed'}
$adapterDeadline=(Get-Date).AddSeconds(10)
do{
 $lines=Read-KkFreshLog -Path "$root\adapter.log" -Offset $adapterOffset
 if($lines -match ('(?m)^pid='+$sdk[0].ProcessId+' stage=ready\r?$')){break}
 Start-Sleep -Milliseconds 100
}while((Get-Date)-lt$adapterDeadline)
if($lines -notmatch ('(?m)^pid='+$sdk[0].ProcessId+' stage=ready\r?$')){throw 'Request adapter not ready'}
$inputOffset=if(Test-Path -LiteralPath "$root\input-provider.log"){(Get-Item -LiteralPath "$root\input-provider.log").Length}else{0}
& 'C:\KK-Lab\kk_inject.exe' "$root\input-provider-a07.dll" --pid $sdk[0].ProcessId *> "$run\input-provider-inject.txt"
if($LASTEXITCODE){throw 'User-mode input adapter injection failed'}
$inputDeadline=(Get-Date).AddSeconds(95)
do{
 $inputState=Read-KkFreshLog -Path "$root\input-provider.log" -Offset $inputOffset
 if($inputState -match ('pid='+$sdk[0].ProcessId+' .*stage=user_mode_event_adapter_ready')){break}
 Start-Sleep -Milliseconds 100
}while((Get-Date)-lt$inputDeadline)
if($inputState -notmatch ('pid='+$sdk[0].ProcessId+' .*stage=user_mode_event_adapter_ready')){throw 'User-mode input adapter not ready'}
# Observe only endpoint names, before the user's first password submission.
& 'C:\KK-Lab\kk_inject.exe' "$root\http-path-a07.dll" --pid $sdk[0].ProcessId *> "$run\http-path-inject.txt"
if($LASTEXITCODE){throw 'Path-only observer injection failed'}
# This DLL only observes RoleProperty. It never invokes the old success bypass.
& 'C:\KK-Lab\kk_inject.exe' "$root\observer.dll" --pid $p.Id *> "$run\observer-inject.txt"
if($LASTEXITCODE){throw 'Read-only observer failed'}
$sdkNow=@(Get-CimInstance Win32_Process -Filter "Name='sdologin.exe'"|Where-Object {$_.ProcessId -eq $sdk[0].ProcessId -and $_.ParentProcessId -eq $p.Id -and $_.ExecutablePath -ieq "$client\sdo\sdologin\sdologin.exe"})
if($sdkNow.Count -ne 1){throw 'SDK identity changed during preparation'}
$p.Refresh();if($p.HasExited){throw 'Game exited during preparation'}
$ready=@{schema='kk-sdo-compatibility-ready-v1';status='ready';authentication_mode=$AuthenticationMode;pid=$p.Id;sdk_pid=$sdkNow[0].ProcessId;sdk_creation_utc=$sdkNow[0].CreationDate.ToUniversalTime().ToString('o');client_root=$client;request_adapter_ready=$true;input_adapter_ready=$true;request_log_offset=$adapterOffset;input_log_offset=$inputOffset}
$ready|ConvertTo-Json|Set-Content -LiteralPath "$run\compatibility-ready.json" -Encoding utf8
@{status=if($AuthenticationMode -eq 'LocalApi'){'awaiting_local_api_auth'}else{'awaiting_user_password'};authentication_mode=$AuthenticationMode;pid=$p.Id;sdk_pid=$sdkNow[0].ProcessId;run=$run}|ConvertTo-Json|Set-Content -LiteralPath "$root\latest-entry.stage.json" -Encoding utf8
Move-Item -LiteralPath "$root\latest-entry.stage.json" -Destination "$root\latest-entry.json" -Force
