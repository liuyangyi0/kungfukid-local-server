# Pure policy-model tests. No VM, firewall mutation, or client launch.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Set-KkNativeEgress.ps1') -Action Plan -ClientRoot 'C:\KK-Lab\model' -ServerAddress '203.0.113.10' | Out-Null
$expected=@(New-EgressPlan 'C:\KK-Lab\model' '203.0.113.10' 17999 18000 18001 18001)
function Get-Service { [pscustomobject]@{Status='Running'} }
$script:bad=''
function Get-NetFirewallProfile {
 @('Domain','Private','Public') | ForEach-Object { [pscustomobject]@{Name=$_;Enabled=($script:bad -ne 'disabled');DefaultOutboundAction='Block'} }
}
function Get-NetFirewallRule {
 foreach($r in $expected){[pscustomobject]@{Name=$r.name;Group='KK Native Cloud Egress v1';Profile='Any'}}
 if($script:bad -eq 'extra'){[pscustomobject]@{Name='foreign';Group='foreign';Profile='Any'}}
}
function Get-NetFirewallApplicationFilter {
 param([Parameter(ValueFromPipeline)]$InputObject)
 process{$r=$expected | Where-Object name -eq $InputObject.Name;[pscustomobject]@{Program=$(if($script:bad -eq 'program'){'Any'}else{$r.program});Package='Any'}}
}
function Get-NetFirewallPortFilter {
 param([Parameter(ValueFromPipeline)]$InputObject)
 process{$r=$expected | Where-Object name -eq $InputObject.Name;[pscustomobject]@{Protocol=$r.protocol;RemotePort=$(if($script:bad -eq 'port'){'Any'}else{[string]$r.port});LocalPort='Any'}}
}
function Get-NetFirewallAddressFilter {
 param([Parameter(ValueFromPipeline)]$InputObject)
 process{[pscustomobject]@{RemoteAddress=$(if($script:bad -eq 'address'){'Any'}else{'203.0.113.10'});LocalAddress='Any'}}
}
Assert-Policy $expected
foreach($case in @('disabled','extra','program','port','address')){
 $script:bad=$case;$rejected=$false
 try{Assert-Policy $expected}catch{$rejected=$true}
 if(-not $rejected){throw ('Policy unexpectedly accepted '+$case)}
}
foreach($address in @('::1','0.0.0.0','224.0.0.1','1.2.3.4/24','1.2.3.4,5.6.7.8')){
 $rejected=$false;try{New-EgressPlan 'C:\KK-Lab\model' $address 17999 18000 18001 18001 | Out-Null}catch{$rejected=$true}
 if(-not $rejected){throw 'Invalid endpoint accepted'}
}
'PASS: policy plan and10 fail-closed cases; no firewall commands executed.'
