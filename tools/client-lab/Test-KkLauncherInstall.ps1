[CmdletBinding()]
param([Parameter(Mandatory)][string]$LauncherExecutable)
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('kk-launcher-install-'+[Guid]::NewGuid().ToString('N'))
$client=Join-Path $root 'game with spaces'
New-Item -ItemType Directory -Path $client -Force|Out-Null
try{
 # Deliberately non-executable fixture bytes. Nothing is launched or loaded.
 [IO.File]::WriteAllBytes((Join-Path $client 'gfxz-lab.exe'),[byte[]]@(0))
 $options=@{auth_host='localhost';sdk_host='127.0.0.1';auth_port=17999;sdk_port=18000;game_port=18001;udp_port=18001;initializer_log=(Join-Path $root 'initializer.log');initializer_manual_start=$true}
 foreach($name in @('adapter_dll','injector_path','initializer_dll','certificate_path')){$path=Join-Path $root $name;[IO.File]::WriteAllText($path,'fixture-only');$options[$name]=$path}
 $settings=Join-Path $root 'settings.json';[IO.File]::WriteAllText($settings,($options|ConvertTo-Json))
 & (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null
 $installed=Get-Content -LiteralPath (Join-Path $client 'launcher\launcher.json') -Raw|ConvertFrom-Json
 foreach($name in @('adapter_dll','injector_path','initializer_dll','certificate_path')){
  if([IO.Path]::IsPathRooted($installed.$name)-or-not(Test-Path -LiteralPath (Join-Path $client ('launcher\'+$installed.$name)))){throw 'Installed bundle path is not portable'}
 }
 $entry=Join-Path $client '功夫小子启动器.exe'
 if(-not(Test-Path -LiteralPath $entry)-or-not(Test-Path -LiteralPath ($entry+'.config'))){throw 'EXE entry is missing'}
 if(@(Get-ChildItem -LiteralPath $client -Recurse -File|Where-Object{$_.Extension-in@('.cmd','.ps1','.cs')}).Count){throw 'User bundle must not require scripts or compiler sources'}
 # Invalid server configuration must be rejected before replacing any installed file.
 $installedConfig=Join-Path $client 'launcher\launcher.json'
 $beforeConfig=[IO.File]::ReadAllText($installedConfig)
 $installedTool=Join-Path $client 'launcher\native-tools\kk_native_cloud_ticket.dll'
 $beforeTool=[IO.File]::ReadAllText($installedTool)
 [IO.File]::WriteAllText($options.adapter_dll,'replacement-must-not-be-copied')
 foreach($bad in @(@{key='auth_port';value=70000},@{key='sdk_host';value='not-an-ip'},@{key='game_port';value=18000})){
  $saved=$options[$bad.key];$options[$bad.key]=$bad.value
  [IO.File]::WriteAllText($settings,($options|ConvertTo-Json));$rejected=$false
  try{& (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null}catch{$rejected=$true}
  $options[$bad.key]=$saved
  if(-not$rejected){throw ('Installer accepted invalid setting: '+$bad.key)}
  if([IO.File]::ReadAllText($installedConfig)-ne$beforeConfig-or[IO.File]::ReadAllText($installedTool)-ne$beforeTool){throw 'Invalid configuration partially overwrote an existing installation'}
 }
 [IO.File]::WriteAllText($options.adapter_dll,'fixture-only')
 # A failed final configuration replacement must leave the previous JSON intact.
 $locked=[IO.File]::Open($installedConfig,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 $options.auth_port=17998;[IO.File]::WriteAllText($settings,($options|ConvertTo-Json));$rejected=$false
 try{
  try{& (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null}catch{$rejected=$true}
 }finally{$locked.Dispose();$options.auth_port=17999}
 if(-not$rejected-or[IO.File]::ReadAllText($installedConfig)-ne$beforeConfig){throw 'Failed configuration replacement damaged the previous JSON'}
 if(@(Get-ChildItem -LiteralPath (Split-Path -Parent $installedConfig) -Filter 'launcher.*.tmp').Count){throw 'Failed configuration replacement leaked a temporary file'}
 # The portable initializer creates its log next to the installed DLL.
 # No log file is an input or should be copied from a previous machine.
 $options.initializer_log='auto';[IO.File]::WriteAllText($settings,($options|ConvertTo-Json))
 & (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null
 $installed=Get-Content -LiteralPath (Join-Path $client 'launcher\launcher.json') -Raw|ConvertFrom-Json
 $expectedLog=Join-Path $client 'launcher\native-tools\kk1-official-flow.log'
 if($installed.initializer_log-ne'native-tools\kk1-official-flow.log'){throw 'Auto initializer log must remain relative in the installed bundle'}
 if(Test-Path -LiteralPath $expectedLog){throw 'Installer must not invent an initializer readiness log'}
 $options.initializer_manual_start=$false;[IO.File]::WriteAllText($settings,($options|ConvertTo-Json));$rejected=$false
 try{& (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null}catch{$rejected=$true}
 if(-not$rejected){throw 'Auto log accepted an automatic legacy initializer'}
 $options.initializer_manual_start=$true
 # Move only the fixture installation, within the verified temporary workspace.
 $moved=Join-Path $root 'relocated game'
 $resolvedClient=[IO.Path]::GetFullPath($client);$resolvedMoved=[IO.Path]::GetFullPath($moved)
 $prefix=[IO.Path]::GetFullPath($root).TrimEnd('\')+'\'
 if(-not$resolvedClient.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)-or-not$resolvedMoved.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe fixture move'}
 Move-Item -LiteralPath $client -Destination $moved
 $client=$moved
 $assembly=[Reflection.Assembly]::LoadFile([IO.Path]::GetFullPath($LauncherExecutable))
 $bootstrap=$assembly.GetType('KKLocalAccounts.LauncherBootstrap',$true)
 $loaded=$bootstrap.GetMethod('LoadOptions').Invoke($null,[object[]]@([string](Join-Path $client 'launcher\launcher.json')))
 if($loaded.initializer_log-ne(Join-Path $client 'launcher\native-tools\kk1-official-flow.log')){throw 'Moving the complete installation left a stale initializer log path'}
 $options['password']='not-a-real-secret';[IO.File]::WriteAllText($settings,($options|ConvertTo-Json));$rejected=$false
 try{& (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null}catch{$rejected=$true}
 if(-not$rejected){throw 'Installer accepted secret-bearing settings'}
 'PASS: portable EXE bundle and relocation; invalid endpoint/port/secret settings rejected before replacement; failed configuration commit preserves JSON; no game or VM started.'
}finally{
 $resolved=[IO.Path]::GetFullPath($root);$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath())
 if($resolved.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase)-and[IO.Path]::GetFileName($resolved)-match'^kk-launcher-install-[0-9a-f]{32}$'){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
