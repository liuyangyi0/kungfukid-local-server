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
if($Action -eq 'Check'){Assert-Policy $plan;Write-Output 'KK_NATIVE_EGRESS_READY';return}
Assert-Admin;Assert-Idle
$receiptPath=[IO.Path]::GetFullPath($Receipt)
if(-not $receiptPath.StartsWith('C:\KK-Lab\',[StringComparison]::OrdinalIgnoreCase)){throw 'Receipt must remain inside guest lab'}
if($Action -eq 'Apply'){
 if(Test-Path -LiteralPath $receiptPath){throw 'Existing policy receipt: Check or Restore it; do not overwrite'}
 foreach($row in $plan){if(-not(Test-Path -LiteralPath $row.program -PathType Leaf)){throw 'Allowlisted executable missing'}}
 if(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue){throw 'Unexpected existing native policy'}
 $allowed=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Enabled True -Action Allow)
 $before=@(Get-NetFirewallProfile -PolicyStore PersistentStore | Select-Object Name,Enabled,DefaultOutboundAction)
 # Save rollback data before any mutation; interruption leaves a recoverable,
 # fail-closed policy. Never auto-open networking after an error.
 [IO.File]::WriteAllText($receiptPath,([pscustomobject]@{schema='kk-native-egress-receipt-v1';computer=$env:COMPUTERNAME;profiles=$before;disabled=@($allowed.Name);rules=$plan}|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
 Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True -DefaultOutboundAction Block
 foreach($rule in $allowed){Disable-NetFirewallRule -PolicyStore PersistentStore -Name $rule.Name | Out-Null}
 foreach($row in $plan){New-NetFirewallRule -PolicyStore PersistentStore -Name $row.name -DisplayName $row.name -Group $group -Profile Any -Direction Outbound -Action Allow -Enabled True -Program $row.program -Protocol $row.protocol -RemoteAddress $row.address -RemotePort $row.port | Out-Null}
 Assert-Policy $plan
 Write-Output 'KK_NATIVE_EGRESS_READY'
 return
}
$saved=Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
if($saved.schema -ne 'kk-native-egress-receipt-v1' -or $saved.computer -ne $env:COMPUTERNAME){throw 'Foreign policy receipt refused'}
foreach($row in $saved.rules){
 if($row.name -notin @('KKNative-Auth','KKNative-SDK','KKNative-Game','KKNative-Relay')){throw 'Invalid rollback rule'}
 Remove-NetFirewallRule -PolicyStore PersistentStore -Name $row.name -ErrorAction SilentlyContinue
}
foreach($name in $saved.disabled){Enable-NetFirewallRule -PolicyStore PersistentStore -Name $name | Out-Null}
foreach($profile in $saved.profiles){Set-NetFirewallProfile -Profile $profile.Name -Enabled $profile.Enabled -DefaultOutboundAction $profile.DefaultOutboundAction}
# Keep the rollback receipt as an audit artifact, move only the exact named file.
Move-Item -LiteralPath $receiptPath -Destination ($receiptPath+'.restored-'+[DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))
Write-Output 'KK_NATIVE_EGRESS_RESTORED'
