<#
Local account UI: separate login/registration, optional nickname, masked password,
retryable region discovery, explicit entry progress, and expired-session recovery.
Uses kk-local-auth-v1 without persisting passwords or tokens. The original SDK
initialization bridge is still required; this is not a replacement SDK.
Run Test-KkLocalAccountWindow.ps1 for isolated UI/API-fixture validation.
Deploy this script and LocalAccountWindow.cs together; editing host source alone
does not update an already installed VM copy.
#>
[CmdletBinding()]
param([int]$AuthPort=7999,
      [string]$ClientRoot='C:\KK-Lab\kk1-lobby-client-20260911',
      [string]$LabRoot='C:\KK-Lab',
      [string]$EventsPath='C:\KK-Lab\password-service\Game.jsonl',
      [string]$NativeCloudSettings='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing,System.Web.Extensions,System.Xml
Add-Type -Path @((Join-Path $PSScriptRoot 'LocalAccountWindow.cs'),(Join-Path $PSScriptRoot 'NativeCloudHandoff.cs')) -ReferencedAssemblies System,System.Windows.Forms,System.Drawing,System.Web.Extensions,System.Core,System.Xml
if($NativeCloudSettings){
 $json=[IO.File]::ReadAllText([IO.Path]::GetFullPath($NativeCloudSettings))
 $settings=(New-Object System.Web.Script.Serialization.JavaScriptSerializer).Deserialize($json,[KKLocalAccounts.NativeCloudOptions])
 $settings.Validate()
 [KKLocalAccounts.AccountWindow]::RunNative($settings,$ClientRoot,$LabRoot,$EventsPath)
}else{
 [KKLocalAccounts.AccountWindow]::Run($AuthPort,$ClientRoot,$LabRoot,$EventsPath)
}
