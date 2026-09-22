# All state lives in memory. No Windows firewall/file backend is loaded.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'KkNativeEgressTransaction.ps1')
function Clone($x){if($null -eq $x){return $null};$x | ConvertTo-Json -Depth 24 | ConvertFrom-Json}
$plan=@([pscustomobject]@{name='a';program='client';protocol='TCP';address='192.0.2.1';port=1000},[pscustomobject]@{name='b';program='client';protocol='UDP';address='192.0.2.1';port=1001})
function Reset-Model {
 $script:state=@{journal=$null;guard=$false;deny=$false;enabled=$false;old=@{one=$true;two=$true};owned=@{};changed=$false;calls=0;fail=0;failed=$false}
}
$backend={param($op,$arg)
 $s=$script:state
 $mutating=$op -in @('Save','EnsureGuard','SetDefaultDeny','DisableOriginal','EnsureAllow','RemoveGuard','RemoveAllow','RestoreOriginal','RestoreDefaults','RestoreEnabled')
 if($mutating){$s.calls++;if($s.fail -eq $s.calls -and -not $s.failed){$s.failed=$true;throw ('Injected interruption at '+$op)}}
 switch($op){
  Load {return (Clone $s.journal)}
  Save {$s.journal=Clone $arg;return}
  Snapshot {return [pscustomobject]@{rules=@([pscustomobject]@{name='one';signature='one'},[pscustomobject]@{name='two';signature='two'});profiles=@([pscustomobject]@{name='Model';enabled='False';outbound='Allow'})}}
  EnsureGuard {$s.guard=$true;$s.enabled=$true;return}
  VerifyGuard {if(-not $s.guard -or -not $s.enabled){throw 'not guarded'};return}
  SetDefaultDeny {$s.deny=$true;return}
  DisableOriginal {if($s.changed){throw 'external conflict'};$s.old[$arg.rule.name]=$false;return}
  EnsureAllow {if($s.owned.ContainsKey($arg.rule.name) -and $s.owned[$arg.rule.name] -ne $arg.journal.id){throw 'unowned rule'};$s.owned[$arg.rule.name]=$arg.journal.id;return $arg.rule.name}
  VerifyPolicy {if(-not $s.deny -or @($s.old.Values | Where-Object {$_}).Count -or $s.owned.Count -ne $plan.Count){throw 'policy mismatch'};return}
  RemoveGuard {$s.guard=$false;return}
  VerifyComplete {if($s.guard -or -not $s.deny -or -not $s.enabled -or $s.owned.Count -ne $plan.Count -or @($s.old.Values | Where-Object {$_}).Count){throw 'not complete'};return}
  CheckRestoreConflicts {if($s.changed){throw 'external conflict'};return}
  RemoveAllow {if($s.owned.ContainsKey($arg.rule.name) -and $s.owned[$arg.rule.name] -ne $arg.journal.id){throw 'foreign owner'};$s.owned.Remove($arg.rule.name);return}
  RestoreOriginal {$s.old[$arg.rule.name]=$true;return}
  RestoreDefaults {$s.deny=$false;return}
  RestoreEnabled {$s.enabled=$false;return}
  VerifyRestored {if($s.guard -or $s.deny -or $s.enabled -or $s.owned.Count -or @($s.old.Values | Where-Object {-not $_}).Count){throw 'not restored'};return}
  default {throw ('Unknown model command '+$op)}
 }
}
function Run($action){Invoke-KkEgressTransaction -Action $action -Plan $plan -Machine 'MODEL' -Backend $backend}
Reset-Model
if((Run Apply) -ne 'KK_NATIVE_EGRESS_READY'){throw 'Apply failed'}
$applySteps=$state.calls
if((Run Check) -ne 'KK_NATIVE_EGRESS_READY'){throw 'Check failed'}
Run Apply | Out-Null
if($state.calls -ne $applySteps){throw 'Completed Apply was not idempotent'}
$before=$state.calls;Run Restore | Out-Null;$restoreSteps=$state.calls-$before
Run Restore | Out-Null
for($fail=1;$fail -le $applySteps;$fail++){
 Reset-Model;$state.fail=$fail
 try{Run Apply | Out-Null;throw 'Failure was not injected'}catch{if(-not $state.failed){throw}}
 if($state.journal -and $state.journal.error -eq 'failed_guarded' -and -not $state.guard){throw 'Lost guard on failed Apply'}
 $refused=$false;try{Run Check | Out-Null}catch{$refused=$true};if(-not $refused){throw 'Partial transaction passed Check'}
 $state.fail=0;Run Apply | Out-Null;Run Check | Out-Null;Run Restore | Out-Null
}
for($fail=1;$fail -le $restoreSteps;$fail++){
 Reset-Model;Run Apply | Out-Null;$state.fail=$state.calls+$fail
 try{Run Restore | Out-Null;throw 'Restore failure was not injected'}catch{if(-not $state.failed){throw}}
 $state.fail=0;Run Restore | Out-Null;Run Restore | Out-Null
}
Reset-Model;Run Apply | Out-Null;$state.changed=$true
$refused=$false;try{Run Restore | Out-Null}catch{$refused=$true}
if(-not $refused -or -not $state.guard -or $state.owned.Count -ne $plan.Count){throw 'External conflict was overwritten'}
$state.changed=$false;Run Restore | Out-Null
Reset-Model;Run Apply | Out-Null
$other=Clone $plan;$other[0].port=9999;$rejected=$false
try{Invoke-KkEgressTransaction -Action Apply -Plan $other -Machine 'MODEL' -Backend $backend | Out-Null}catch{$rejected=$true}
if(-not $rejected){throw 'Changed plan adopted existing transaction'}
$state.journal.machine='FOREIGN';$rejected=$false;try{Run Check | Out-Null}catch{$rejected=$true};if(-not $rejected){throw 'Foreign receipt accepted'}
Write-Output ('PASS: '+$applySteps+' Apply interruption points; '+$restoreSteps+' Restore interruption points; retry/idempotence; partial Check rejection; external conflict; plan and machine binding. Memory backend only.')
Reset-Model;Run Apply | Out-Null
$state.journal.phase='ready_to_commit' # power loss after removing guard but before completion receipt
$rejected=$false;try{Run Check | Out-Null}catch{$rejected=$true};if(-not $rejected){throw 'Commit boundary authorized a client'}
Run Apply | Out-Null;Run Check | Out-Null
Run Restore | Out-Null;$state.journal.phase='restore_ready'
Run Restore | Out-Null
Write-Output 'PASS: persistent commit-boundary crash recovery for Apply and Restore.'
