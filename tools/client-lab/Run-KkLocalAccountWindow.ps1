<#
Local account UI: separate login/registration, optional nickname, masked password,
retryable region discovery, explicit entry progress, and expired-session recovery.
Uses kk-local-auth-v1 without persisting passwords or tokens. The original SDK
initialization bridge is still required; this is not a replacement SDK.
Run Test-KkLocalAccountWindow.ps1 for isolated UI/API-fixture validation.
Deploy this script and LocalAccountWindow.cs together; editing host source alone
does not update an already installed client copy.
#>
[CmdletBinding()]
param([int]$AuthPort=7999,
      [string]$ClientRoot='',
      [string]$LabRoot='',
      [string]$EventsPath='',
      [string]$NativeCloudSettings='', [switch]$LegacyLocal)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing,System.Web.Extensions,System.Xml
trap {[void][Windows.Forms.MessageBox]::Show(('启动器未能启动：'+$_.Exception.Message),'功夫小子启动器',[Windows.Forms.MessageBoxButtons]::OK,[Windows.Forms.MessageBoxIcon]::Error);exit 1}
if(-not $ClientRoot){
 $picker=New-Object Windows.Forms.FolderBrowserDialog
 $picker.Description='选择已安装启动器的游戏目录（含 gfxz-lab.exe 和 launcher 文件夹）'
 try{if($picker.ShowDialog()-ne[Windows.Forms.DialogResult]::OK){return};$ClientRoot=$picker.SelectedPath}finally{$picker.Dispose()}
}
$ClientRoot=[IO.Path]::GetFullPath($ClientRoot)
if(-not $LabRoot){$LabRoot=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'KungFuKid\Launcher'}
if(-not $LegacyLocal -and -not $NativeCloudSettings){$NativeCloudSettings=Join-Path $ClientRoot 'launcher\launcher.json'}
if($NativeCloudSettings -and -not(Test-Path -LiteralPath $NativeCloudSettings -PathType Leaf)){throw '缺少 launcher/launcher.json，请先安装启动器；不会回退到旧登录。'}
New-Item -ItemType Directory -Path $LabRoot -Force | Out-Null
Add-Type -Path @((Join-Path $PSScriptRoot 'LocalAccountWindow.cs'),(Join-Path $PSScriptRoot 'NativeCloudHandoff.cs')) -ReferencedAssemblies System,System.Windows.Forms,System.Drawing,System.Web.Extensions,System.Core,System.Xml
if($NativeCloudSettings){
 $json=[IO.File]::ReadAllText([IO.Path]::GetFullPath($NativeCloudSettings))
 $settings=(New-Object System.Web.Script.Serialization.JavaScriptSerializer).Deserialize($json,[KKLocalAccounts.NativeCloudOptions])
 $base=Split-Path -Parent ([IO.Path]::GetFullPath($NativeCloudSettings))
 foreach($field in @('adapter_dll','injector_path','initializer_dll','certificate_path','initializer_log')){
  $value=$settings.$field
  if($value -and -not[IO.Path]::IsPathRooted($value)){$settings.$field=[IO.Path]::GetFullPath((Join-Path $base $value))}
 }
 $settings.Validate()
 [KKLocalAccounts.AccountWindow]::RunNative($settings,$ClientRoot,$LabRoot,$EventsPath)
}else{
 [KKLocalAccounts.AccountWindow]::Run($AuthPort,$ClientRoot,$LabRoot,$EventsPath)
}
