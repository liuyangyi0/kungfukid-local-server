<# Guest-only default-deny egress for the native cloud test.
No host firewall changes, no driver, no network bridge, no retired endpoint list.
Apply requires elevation and an idle VMware guest. Changes persist across a
launcher crash. Restore is explicit and requires no running original client.
#>
[CmdletBinding()]
param(
 [ValidateSet('Plan','Apply','Check','Restore')][string]$Action='Plan',
 [string]$ClientRoot='C:\KK-Lab\dual-vm-client-20260919',
 [string]$ServerAddress='127.0.0.1',
 [ValidateRange(1,65535)][int]$AuthPort=17999,
 [ValidateRange(1,65535)][int]$SdkPort=18000,
 [ValidateRange(1,65535)][int]$GamePort=18001,
 [ValidateRange(1,65535)][int]$UdpPort=18001,
 [string]$Receipt='C:\KK-Lab\native-egress-policy.json'
)
$ErrorActionPreference='Stop'
$group='KK Native Cloud Egress v1'
function New-EgressPlan {
 param([string]$Root,[string]$Address,[int]$Auth,[int]$Sdk,[int]$Game,[int]$Udp)
 $ip=$null
 if(-not [Net.IPAddress]::TryParse($Address,[ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $Address -ne $ip.ToString()){throw 'A canonical IPv4 service address is required'}
 $bytes=$ip.GetAddressBytes()
 if($bytes[0] -eq 0 -or $bytes[0] -ge 224 -or $Address -eq '255.255.255.255'){throw 'Unicast service address required'}
 if(@($Auth,$Sdk,$Game | Select-Object -Unique).Count -ne 3){throw 'Distinct TCP ports required'}
 $full=[IO.Path]::GetFullPath($Root).TrimEnd('\')
 if(-not $full.StartsWith('C:\KK-Lab\',[StringComparison]::OrdinalIgnoreCase)){throw 'An isolated C:\KK-Lab client copy is required'}
 $shell=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
 @(
  [pscustomobject]@{name='KKNative-Auth';program=$shell;protocol='TCP';port=$Auth;address=$Address},
  [pscustomobject]@{name='KKNative-SDK';program=(Join-Path $full 'gfxz-lab.exe');protocol='TCP';port=$Sdk;address=$Address},
  [pscustomobject]@{name='KKNative-Game';program=(Join-Path $full 'gfxz-lab.exe');protocol='TCP';port=$Game;address=$Address},
  [pscustomobject]@{name='KKNative-Relay';program=(Join-Path $full 'gfxz-lab.exe');protocol='UDP';port=$Udp;address=$Address}
 )
}
function Assert-Guest {
 $machine=Get-CimInstance Win32_ComputerSystem
 if($machine.Manufacturer -notmatch 'VMware' -or $machine.Model -notmatch 'VMware'){throw 'Guest-only: VMware identity required; host changes refused'}
 if(-not(Test-Path -LiteralPath 'C:\KK-Lab' -PathType Container)){throw 'KK-Lab guest root missing'}
}
function Assert-Idle {
 if(Get-Process -Name 'gfxz-lab','Client','sdologin','Launcher' -ErrorAction SilentlyContinue){throw 'Close original clients and SDK launchers before changing egress policy'}
}
function Assert-Admin {
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 if(-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Elevated guest shell required'}
}
function Assert-Policy($Plan) {
 if((Get-Service MpsSvc).Status -ne 'Running'){throw 'Windows Firewall is not running'}
 $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
 if($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled -ne $true -or $_.DefaultOutboundAction -ne 'Block' }).Count){throw 'All effective firewall profiles must be enabled and default-deny outbound'}
 $allow=@(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Outbound -Enabled True -Action Allow)
 if($allow.Count -ne $Plan.Count){throw 'Unexpected effective outbound allow rules: fail closed'}
 foreach($want in $Plan){
  $actual=@($allow | Where-Object Name -eq $want.name)
  if($actual.Count -ne 1 -or $actual[0].Group -ne $group -or $actual[0].Profile -ne 'Any'){throw 'Egress rule identity mismatch'}
  $app=$actual[0] | Get-NetFirewallApplicationFilter
  $port=$actual[0] | Get-NetFirewallPortFilter
  $addr=$actual[0] | Get-NetFirewallAddressFilter
  if($app.Program -ne $want.program -or $app.Package -notin @('Any',$null,'') -or $port.Protocol -ne $want.protocol -or [string]$port.RemotePort -ne [string]$want.port -or [string]$port.LocalPort -ne 'Any' -or [string]$addr.RemoteAddress -ne $want.address -or [string]$addr.LocalAddress -ne 'Any'){throw 'Egress rule scope mismatch'}
 }
}
$plan=@(New-EgressPlan $ClientRoot $ServerAddress $AuthPort $SdkPort $GamePort $UdpPort)
if($Action -eq 'Plan'){
 [pscustomobject]@{schema='kk-native-egress-plan-v1';default_outbound='Block';disable_existing_outbound_allows=$true;ipv6_allowed=$false;dns_allowed=$false;rules=$plan}|ConvertTo-Json -Depth 5
 return
}
Assert-Guest
$receiptPath=[IO.Path]::GetFullPath($Receipt)
if(-not $receiptPath.StartsWith('C:\\KK-Lab\\',[StringComparison]::OrdinalIgnoreCase)){throw 'Receipt must remain inside guest lab'}
for($directory=[IO.DirectoryInfo]::new([IO.Path]::GetDirectoryName($receiptPath));$null -ne $directory;$directory=$directory.Parent){
 if($directory.Exists -and ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked journal directory refused'}
}
if(Test-Path -LiteralPath $receiptPath){if((Get-Item -LiteralPath $receiptPath).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked receipt refused'}}
if($Action -ne 'Check'){Assert-Admin;Assert-Idle}
. (Join-Path $PSScriptRoot 'KkNativeEgressTransaction.ps1')
. (Join-Path $PSScriptRoot 'KkNativeEgressWindows.ps1')
$mutex=[Threading.Mutex]::new($false,'Global\KKNativeEgressPolicyV2')
$held=$false
try{
 try{$held=$mutex.WaitOne(15000)}catch [Threading.AbandonedMutexException]{$held=$true}
 if(-not $held){throw 'Another policy transaction is active'}
 Invoke-KkEgressTransaction -Action $Action -Plan $plan -Machine $env:COMPUTERNAME -Backend {param($op,$arg) Invoke-KkWindowsBackend $op $arg}
}finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
