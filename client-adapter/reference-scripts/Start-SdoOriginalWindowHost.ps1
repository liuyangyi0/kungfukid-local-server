[CmdletBinding()]
param([ValidateSet('OriginalSdk','LocalApi')][string]$AuthenticationMode='OriginalSdk')
$ErrorActionPreference='Stop'
$c=Import-Clixml -LiteralPath 'D:\KungFuKid-Lab\Secrets\kk-vm-credential.clixml'
& 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe' -T ws -gu $c.UserName -gp $c.GetNetworkCredential().Password runProgramInGuest 'D:\KungFuKid-Lab\VMs\KK-M1-Semantic-Probe\KK-M1-Semantic-Probe.vmx' -noWait -interactive 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File 'C:\KK-Lab\Start-SdoOriginalWindowGuest.ps1' -AuthenticationMode $AuthenticationMode
if($LASTEXITCODE){throw 'Unable to launch original login in VM'}
if($AuthenticationMode -eq 'LocalApi'){
 'Compatibility preparation is starting inside the VM. Wait for compatibility-ready.json before local API authentication.'
}else{
 'Original login is starting inside the VM. Enter credentials there, not in chat.'
}
