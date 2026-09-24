[CmdletBinding()]
param([Parameter(Mandatory)][string]$ClientRoot,
      [Parameter(Mandatory)][string]$NativeCloudSettings,
      [Parameter(Mandatory)][string]$LauncherExecutable,
      [switch]$DesktopShortcut)
$ErrorActionPreference='Stop'
$client=[IO.Path]::GetFullPath($ClientRoot)
$launcherExe=[IO.Path]::GetFullPath($LauncherExecutable)
if(-not(Test-Path -LiteralPath $launcherExe -PathType Leaf)-or-not(Test-Path -LiteralPath ($launcherExe+'.config') -PathType Leaf)){throw 'Build-KkLauncher output EXE and EXE.config are required'}
if(-not(Test-Path -LiteralPath (Join-Path $client 'gfxz-lab.exe') -PathType Leaf)){throw 'Prepared client gfxz-lab.exe is required; this installer does not modify original game binaries'}
$settingsPath=[IO.Path]::GetFullPath($NativeCloudSettings)
$source=Split-Path -Parent $settingsPath
$settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$allowed=@('auth_host','sdk_host','auth_port','sdk_port','game_port','udp_port','adapter_dll','injector_path','initializer_dll','certificate_path','initializer_log','initializer_manual_start','development_only')
if(@($settings.PSObject.Properties|Where-Object{$_.Name-notin$allowed}).Count){throw 'Unknown launcher setting; credentials and arbitrary fields must not be copied into installation'}
# Reuse the supplied launcher's configuration validation without running Main.
# This happens before copying tools or overwriting an existing installation.
$assembly=[Reflection.Assembly]::LoadFile($launcherExe)
$optionType=$assembly.GetType('KKLocalAccounts.NativeCloudOptions',$true)
$validation=[Activator]::CreateInstance($optionType)
try{
 foreach($property in $settings.PSObject.Properties){
  $field=$optionType.GetField($property.Name);$value=$property.Value
  if($null-ne$value){$value=[Convert]::ChangeType($value,$field.FieldType,[Globalization.CultureInfo]::InvariantCulture)}
  if($property.Name-in@('adapter_dll','injector_path','initializer_dll','certificate_path','initializer_log')-and$value){
   if(-not[IO.Path]::IsPathRooted($value)){$value=Join-Path $source $value}
   $value=[IO.Path]::GetFullPath($value)
  }
  $field.SetValue($validation,$value)
 }
 [void]$optionType.GetMethod('Validate').Invoke($validation,$null)
}catch{throw ('Invalid launcher configuration: '+$_.Exception.GetBaseException().Message)}
$files=@{}
foreach($field in @('adapter_dll','injector_path','initializer_dll','certificate_path')){
 $property=$settings.PSObject.Properties[$field]
 if(-not$property -or -not$property.Value){if($field-eq'certificate_path'){continue};throw "Missing $field"}
 $path=[string]$property.Value;if(-not[IO.Path]::IsPathRooted($path)){$path=Join-Path $source $path}
 $path=[IO.Path]::GetFullPath($path)
 if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing $field file"}
 $files[$field]=$path
}
if(-not$settings.PSObject.Properties['initializer_log'] -or -not$settings.initializer_log){throw 'initializer_log must match the installed initializer build'}
$destination=Join-Path $client 'launcher'
if($settings.initializer_log-eq'auto'){
 if(-not$settings.PSObject.Properties['initializer_manual_start'] -or $settings.initializer_manual_start-ne$true){throw 'Auto log requires the portable manual-start initializer'}
 $settings.initializer_log='native-tools\kk1-official-flow.log'
}elseif(-not[IO.Path]::IsPathRooted($settings.initializer_log)){$settings.initializer_log=[IO.Path]::GetFullPath((Join-Path $source $settings.initializer_log))}
$entry=Join-Path $client '功夫小子启动器.exe'
$link=$null
if($DesktopShortcut){
 $shell=New-Object -ComObject WScript.Shell
 $link=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '功夫小子.lnk'))
 if($link.TargetPath-and$link.TargetPath-notin@($entry,(Join-Path $client '开始游戏.cmd'))){throw 'Existing desktop shortcut points elsewhere; installation was not modified'}
}
New-Item -ItemType Directory -Path (Join-Path $destination 'native-tools') -Force | Out-Null
foreach($field in $files.Keys){
 $name=switch($field){'adapter_dll'{'kk_native_cloud_ticket.dll'};'injector_path'{'kk_inject.exe'};'initializer_dll'{'kk1-official-flow.dll'};'certificate_path'{'server.cer'}}
 $target=Join-Path $destination "native-tools\$name"
 if($files[$field]-ne$target){Copy-Item -LiteralPath $files[$field] -Destination $target -Force}
 $settings.$field="native-tools\$name"
}
if($launcherExe-ne$entry){Copy-Item -LiteralPath $launcherExe -Destination $entry -Force;Copy-Item -LiteralPath ($launcherExe+'.config') -Destination ($entry+'.config') -Force}
$utf8=New-Object Text.UTF8Encoding($false)
$configuration=Join-Path $destination 'launcher.json'
$temporary=Join-Path $destination ('launcher.'+[Guid]::NewGuid().ToString('N')+'.tmp')
try{
 [IO.File]::WriteAllText($temporary,($settings|ConvertTo-Json -Depth 6),$utf8)
 if(Test-Path -LiteralPath $configuration){[IO.File]::Replace($temporary,$configuration,[NullString]::Value)}else{[IO.File]::Move($temporary,$configuration)}
}finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Force}}
if($null-ne$link){
 $link.TargetPath=$entry;$link.WorkingDirectory=$client;$link.IconLocation=$entry+',0';$link.Description='登录账号，选择区服，进入游戏';$link.Save()
}
Write-Output ('Launcher installed: '+$entry)
