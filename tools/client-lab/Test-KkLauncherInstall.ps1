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
 $options['password']='not-a-real-secret';[IO.File]::WriteAllText($settings,($options|ConvertTo-Json));$rejected=$false
 try{& (Join-Path $PSScriptRoot 'Install-KkLauncher.ps1') -ClientRoot $client -NativeCloudSettings $settings -LauncherExecutable $LauncherExecutable|Out-Null}catch{$rejected=$true}
 if(-not$rejected){throw 'Installer accepted secret-bearing settings'}
 'PASS: portable EXE bundle with spaced paths, no script/compiler dependency in installation, no game binary modification, secret-bearing settings rejected. No game or VM started.'
}finally{
 $resolved=[IO.Path]::GetFullPath($root);$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath())
 if($resolved.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase)-and[IO.Path]::GetFileName($resolved)-match'^kk-launcher-install-[0-9a-f]{32}$'){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
