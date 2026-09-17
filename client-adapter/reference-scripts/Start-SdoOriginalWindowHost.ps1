[CmdletBinding()]
param(
    [switch]$OpenVmConsole,
    [string]$VmPath,
    [string]$VmrunPath='C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
)
$ErrorActionPreference='Stop'
# Public template intentionally does not call authenticated guest operations.
# VMware's -gp puts the guest password on a process command line. Run the
# supplied guest script from the already signed-in VM desktop instead.
if($OpenVmConsole){
    if(-not $VmPath -or -not(Test-Path -LiteralPath $VmPath -PathType Leaf)){
        throw 'Provide the path of your own existing .vmx file with -VmPath.'
    }
    if([IO.Path]::GetExtension($VmPath) -ine '.vmx'){throw 'Expected a VMware .vmx file'}
    if(-not(Test-Path -LiteralPath $VmrunPath -PathType Leaf)){throw 'VMware vmrun was not found'}
    & $VmrunPath -T ws start ([IO.Path]::GetFullPath($VmPath)) gui
    if($LASTEXITCODE){throw 'Unable to open the requested VM console'}
}
Write-Output 'No guest credential has been read or passed to a child process.'
Write-Output 'In the signed-in VM desktop, run the following command after the local service is ready:'
Write-Output "& 'C:\KK-Lab\Start-SdoOriginalWindowGuest.ps1' -AuthenticationMode OriginalSdk"
Write-Output 'This host template opens the VM console only; it does not remotely launch the game or authenticate a player.'
