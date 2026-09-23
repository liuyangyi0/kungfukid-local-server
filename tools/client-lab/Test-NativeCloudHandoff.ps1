[CmdletBinding()]
param([Parameter(Mandatory)][string]$BuildDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Web.Extensions,System.Xml
Add-Type -Path (Join-Path $PSScriptRoot 'NativeCloudHandoff.cs') -ReferencedAssemblies System,System.Core,System.Web.Extensions,System.Xml
$policy=[KKLocalAccounts.NativeCloudHandoff].GetMethod('ModuleSnapshotWithRetry',[Reflection.BindingFlags]'NonPublic,Static')
if(-not $policy){throw 'Missing bounded module snapshot retry policy'}
$script:snapshotCalls=0;$script:snapshotPauses=0;$script:snapshotError=24;$script:snapshotSucceeds=3
$factory=[Func[uint32,uint32,IntPtr]]{param($flags,$target) $script:snapshotCalls++;if($flags-ne0x18-or$target-ne7){throw 'snapshot arguments changed'};if($script:snapshotCalls-ge$script:snapshotSucceeds){return [IntPtr]99};return [IntPtr](-1)}
$lastError=[Func[int]]{return $script:snapshotError}
$pause=[Action[int]]{param($ms) if($ms-le0-or$ms-gt100){throw 'unbounded snapshot delay'};$script:snapshotPauses++}
if($policy.Invoke($null,@([uint32]7,$factory,$lastError,$pause))-ne[IntPtr]99-or$script:snapshotCalls-ne3-or$script:snapshotPauses-ne2){throw 'Transient module retry failed'}
foreach($errorCode in @(5,299,24)){
 $script:snapshotCalls=0;$script:snapshotPauses=0;$script:snapshotError=$errorCode;$script:snapshotSucceeds=[int]::MaxValue;$caught=$null
 try{[void]$policy.Invoke($null,@([uint32]7,$factory,$lastError,$pause))}catch{$caught=$_.Exception;while($caught.InnerException){$caught=$caught.InnerException}}
 if(-not($caught-is[ComponentModel.Win32Exception])-or$caught.NativeErrorCode-ne$errorCode){throw 'Snapshot native error lost'}
 if($errorCode-ne24-and($script:snapshotCalls-ne1-or$script:snapshotPauses-ne0)){throw 'Permanent error retried'}
 if($errorCode-eq24-and($script:snapshotCalls-ne8-or$script:snapshotPauses-ne7)){throw 'Retry not bounded at eight attempts'}
}
$dll=[IO.Path]::GetFullPath((Join-Path $BuildDirectory 'kk_native_cloud_ticket.dll'))
$exe=[IO.Path]::GetFullPath((Join-Path $BuildDirectory 'contract.exe'))
foreach($export in @('KkNativeCloudSetTicket','KkNativeCloudInstall','KkNativeCloudGetStatus','KkNativeCloudGetStatusV2','KkNativeCloudClearTicket','KkNativeCloudRemove')){
 if([KKLocalAccounts.NativeCloudHandoff]::ExportRva($dll,$export) -eq 0){throw 'Missing export'}
}
$xml='<GameClient><Updater><UpdateInfo Url="http://example.invalid/update" /></Updater><RegisterURL URL="http://example.invalid/register"/><LoginServer Ip="192.0.2.1" Port="8000"/></GameClient>'
$updated=[KKLocalAccounts.NativeCloudHandoff]::RewriteConfig($xml,'127.0.0.1',18000)
if($updated -match 'example.invalid|192.0.2.1' -or $updated -notmatch 'Port="18000"'){throw 'Endpoint rewrite failed'}
$start=[Diagnostics.ProcessStartInfo]::new($exe,('--host-adapter "'+$dll+'"'))
$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
$start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true
$child=[Diagnostics.Process]::Start($start)
try{
 $ready=$child.StandardOutput.ReadLineAsync()
 if(-not $ready.Wait(10000) -or $ready.Result -ne 'READY'){throw 'Adapter model host failed'}
 $grant=[Collections.Generic.Dictionary[string,object]]::new()
 $grant['session_expires_at']=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+600
 $grant['game_credential']='1'*64;$grant['udp_credential']='2'*64;$grant['sdk_credential']='3'*64
 $grant['sdk_host']='127.0.0.1';$grant['sdk_port']=18000
 $grant['transport']='kk-aesgcm-v1';$grant['transport_id']='01'*16;$grant['transport_key']='02'*32
 $grant['game_host']='127.0.0.1';$grant['game_port']=18001;$grant['udp_port']=18001
 $packed=[KKLocalAccounts.NativeCloudHandoff]::Pack($child.Id,'ModelUser',$grant,'127.0.0.1',18000)
 try{
  if($packed.Length -ne 224 -or [BitConverter]::ToUInt32($packed,4) -ne 3 -or [BitConverter]::ToUInt32($packed,8) -ne $child.Id){throw 'Configuration layout mismatch'}
  Write-Output 'model:set-ticket'
  if([KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudSetTicket',$packed) -ne 0){throw 'Remote configuration failed'}
 }finally{[Array]::Clear($packed,0,$packed.Length)}
 $status=New-Object byte[] 16
 Write-Output 'model:get-status'
 if([KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudGetStatus',$status) -ne 0 -or [BitConverter]::ToUInt32($status,8) -ne 1){throw 'Remote status failed'}
 if([KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudInstall',$null) -ne 126){throw 'Missing original SDK should fail closed'}
 Write-Output 'model:remove-ticket'
 if([KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudRemove',$null) -ne 0){throw 'Cleanup failed'}
 [void][KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudGetStatus',$status)
 if([BitConverter]::ToUInt32($status,8) -ne 0){throw 'Ticket remained configured'}
 $v2=New-Object byte[] 44
 [Array]::Copy([BitConverter]::GetBytes([uint32]44),0,$v2,0,4);[Array]::Copy([BitConverter]::GetBytes([uint32]2),0,$v2,4,4)
 if([KKLocalAccounts.NativeCloudHandoff]::Invoke($child.Id,$exe,$dll,'KkNativeCloudGetStatusV2',$v2) -ne 0 -or [BitConverter]::ToUInt32($v2,8) -ne 4){throw 'Retired V2 lifecycle status missing'}
 $grant['sdk_port']=18001
 $rejected=$false
 try{[void][KKLocalAccounts.NativeCloudHandoff]::Pack($child.Id,'ModelUser',$grant,'127.0.0.1',18000)}catch{$rejected=$true}
 if(-not $rejected){throw 'Mismatched endpoint accepted'}
 'PASS: TLS controller source compiled; PE exports; config endpoint rewrite;224-byte encrypted handoff; real remote invocation into owned i686 model; missing SDK refusal; ticket cleanup. No game or original SDK loaded.'
}catch{
 Write-Output ('Model failure at '+$_.InvocationInfo.ScriptLineNumber+'; exited='+$child.HasExited)
 if($child.HasExited){Write-Output ('Model exit code='+$child.ExitCode)}
 throw
}finally{
 if(-not $child.HasExited){
  try{$stop=[Threading.EventWaitHandle]::OpenExisting(('Local\KkNativeModel-'+$child.Id));[void]$stop.Set();$stop.Dispose()}catch{}
  if(-not $child.WaitForExit(5000)){$child.Kill();$child.WaitForExit()}
 }
 $child.Dispose()
 if($grant){$grant.Clear()}
}
