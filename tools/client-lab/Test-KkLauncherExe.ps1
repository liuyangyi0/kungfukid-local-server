[CmdletBinding()]
param([Parameter(Mandatory)][string]$LauncherExecutable)
$ErrorActionPreference='Stop'
$exe=[IO.Path]::GetFullPath($LauncherExecutable)
$bytes=[IO.File]::ReadAllBytes($exe);$pe=[BitConverter]::ToInt32($bytes,0x3c)
if([BitConverter]::ToUInt16($bytes,$pe+24+68)-ne2){throw 'Launcher must be Windows GUI subsystem, not console'}
$assembly=[Reflection.Assembly]::LoadFile($exe)
if(-not$assembly.EntryPoint.IsDefined([STAThreadAttribute],$false)){throw 'WinForms entry is not STA'}
$root=Join-Path ([IO.Path]::GetTempPath()) ('kk-exe-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
try{
 Add-Type -AssemblyName System.Windows.Forms,System.Drawing
 [IO.File]::WriteAllText((Join-Path $root 'component.dll'),'model-only')
 $config=Join-Path $root 'launcher.json'
 $settings=@{auth_host='localhost';sdk_host='127.0.0.1';adapter_dll='component.dll';injector_path='component.dll';initializer_dll='component.dll';initializer_log='initializer.log'}
 [IO.File]::WriteAllText($config,($settings|ConvertTo-Json))
 $options=[KKLocalAccounts.LauncherBootstrap]::LoadOptions($config)
 if($options.adapter_dll-ne(Join-Path $root 'component.dll')-or$options.initializer_log-ne(Join-Path $root 'initializer.log')){throw 'EXE-relative config loading failed'}
 if([KKLocalAccounts.LauncherBootstrap]::InstanceName($root)-ne[KKLocalAccounts.LauncherBootstrap]::InstanceName($root+'\.')){throw 'Single-instance root normalization failed'}
 $flags=[Reflection.BindingFlags]'Instance,NonPublic'
 $constructor=[KKLocalAccounts.AccountWindow].GetConstructor($flags,$null,[type[]]@([int],[string],[string],[string]),$null)
 $window=$constructor.Invoke([object[]]@($options.auth_port,[string]$root,[string]$root,[string]''))
 try{
  $window.ConfigureNative($options);$window.ShowInTaskbar=$false;$window.StartPosition=[Windows.Forms.FormStartPosition]::Manual;$window.Location=[Drawing.Point]::new(-32000,-32000);$window.Show();[Windows.Forms.Application]::DoEvents()
  if(-not$window.Visible-or-not[KKLocalAccounts.AccountWindow].GetField('serverSettings',$flags).GetValue($window).Visible){throw 'Compiled login/settings UI did not open'}
 }finally{$window.Dispose()}
 'PASS: compiled x64 GUI entry/STA, relative configuration, per-client instance key and compiled login/settings form. No authentication or game started.'
}finally{
 $resolved=[IO.Path]::GetFullPath($root);$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath())
 if($resolved.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase)-and[IO.Path]::GetFileName($resolved)-match'^kk-exe-test-[0-9a-f]{32}$'){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
