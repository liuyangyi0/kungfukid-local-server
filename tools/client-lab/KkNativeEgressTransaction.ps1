# State machine only. The supplied backend owns all OS/file operations.
function Get-KkPlanKey($Plan){ConvertTo-Json -InputObject @($Plan) -Depth 12 -Compress}
function Invoke-KkEgressTransaction {
 [CmdletBinding()]
 param([ValidateSet('Apply','Check','Restore')][string]$Action,[object[]]$Plan,[string]$Machine,[scriptblock]$Backend)
 $journal=& $Backend 'Load' $null
 if($null -ne $journal){
  if($journal.schema -ne 'kk-native-egress-receipt-v2' -or $journal.machine -ne $Machine){throw 'Foreign or legacy policy receipt; explicit migration required'}
  if((Get-KkPlanKey $journal.plan) -cne (Get-KkPlanKey $Plan)){throw 'A different policy must not overwrite the current transaction'}
 }
 if($Action -eq 'Check'){
  if($null -eq $journal -or $journal.phase -ne 'completed'){throw 'Policy transaction is not completed'}
  & $Backend 'VerifyComplete' $journal
  return 'KK_NATIVE_EGRESS_READY'
 }
 if($null -eq $journal){
  if($Action -eq 'Restore'){throw 'No owned policy transaction to restore'}
  $original=& $Backend 'Snapshot' $null
  $journal=[pscustomobject]@{schema='kk-native-egress-receipt-v2';machine=$Machine;id=[Guid]::NewGuid().ToString('N');plan=@($Plan);original=$original;phase='prepared';disabled=@();created=@();owned=@();error=$null}
  & $Backend 'Save' $journal
 }
 if($journal.phase -eq 'restored'){
  if($Action -eq 'Restore'){& $Backend 'VerifyRestored' $journal;return 'KK_NATIVE_EGRESS_RESTORED'}
  throw 'Archived restored transaction: choose a new receipt for a new Apply'
 }
 if($Action -eq 'Apply' -and $journal.phase -eq 'completed'){
  & $Backend 'VerifyComplete' $journal;return 'KK_NATIVE_EGRESS_READY'
 }
 if($Action -eq 'Apply' -and $journal.phase -like 'restor*'){throw 'Finish Restore before starting another Apply'}
 $guarded=$false
 try{
  & $Backend 'EnsureGuard' $journal
  & $Backend 'VerifyGuard' $journal
  $guarded=$true
  if($Action -eq 'Apply'){
   $journal.phase='guarded';$journal.error=$null;& $Backend 'Save' $journal
   & $Backend 'SetDefaultDeny' $journal
   foreach($rule in @($journal.original.rules)){
    # Persist intent BEFORE mutation; a restart can safely repeat the operation.
    if($journal.disabled -notcontains $rule.name){$journal.disabled+=@($rule.name);& $Backend 'Save' $journal}
    & $Backend 'DisableOriginal' ([pscustomobject]@{journal=$journal;rule=$rule})
   }
   foreach($rule in $journal.plan){
    if($journal.created -notcontains $rule.name){$journal.created+=@($rule.name);& $Backend 'Save' $journal}
    $signature=& $Backend 'EnsureAllow' ([pscustomobject]@{journal=$journal;rule=$rule})
    if(@($journal.owned | Where-Object name -eq $rule.name).Count -eq 0){$journal.owned+=@([pscustomobject]@{name=$rule.name;signature=$signature});& $Backend 'Save' $journal}
   }
   & $Backend 'VerifyPolicy' $journal
   $journal.phase='ready_to_commit';& $Backend 'Save' $journal
   & $Backend 'RemoveGuard' $journal
   & $Backend 'VerifyComplete' $journal
   $journal.phase='completed';& $Backend 'Save' $journal
   return 'KK_NATIVE_EGRESS_READY'
  }
  & $Backend 'CheckRestoreConflicts' $journal
  $journal.phase='restoring';$journal.error=$null;& $Backend 'Save' $journal
  foreach($rule in $journal.plan){if($journal.created -contains $rule.name){& $Backend 'RemoveAllow' ([pscustomobject]@{journal=$journal;rule=$rule})}}
  foreach($rule in @($journal.original.rules)){if($journal.disabled -contains $rule.name){& $Backend 'RestoreOriginal' ([pscustomobject]@{journal=$journal;rule=$rule})}}
  & $Backend 'RestoreDefaults' $journal
  $journal.phase='restore_ready';& $Backend 'Save' $journal
  & $Backend 'RemoveGuard' $journal
  & $Backend 'RestoreEnabled' $journal
  & $Backend 'VerifyRestored' $journal
  $journal.phase='restored';& $Backend 'Save' $journal
  return 'KK_NATIVE_EGRESS_RESTORED'
 }catch{
  # Best-effort re-establishing the guard never widens networking. A power loss
  # at the commit boundary is recovered from ready_to_commit/restore_ready;
  # Check will not authorize a client from either transitional phase.
  if($guarded){try{& $Backend 'EnsureGuard' $journal;& $Backend 'VerifyGuard' $journal}catch{}}
  if($guarded){$journal.phase=if($Action -eq 'Apply'){'guarded'}else{'restoring'}}
  $journal.error=if($guarded){'failed_guarded'}else{'not_applied'}
  try{& $Backend 'Save' $journal}catch{}
  throw
 }
}
