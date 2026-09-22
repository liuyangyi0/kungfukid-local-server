# Windows backend, invoked only by the VMware/admin-guarded entry script.
$guardName='KKNative-Emergency-Block'
function Get-KkFirewallRule($Name){Get-NetFirewallRule -PolicyStore PersistentStore -Name $Name -ErrorAction SilentlyContinue}
function Get-KkRuleSignature($Rule,[switch]$IncludeEnabled){
 $app=$Rule | Get-NetFirewallApplicationFilter
 $port=$Rule | Get-NetFirewallPortFilter
 $address=$Rule | Get-NetFirewallAddressFilter
 $service=$Rule | Get-NetFirewallServiceFilter
 $interface=$Rule | Get-NetFirewallInterfaceFilter
 $type=$Rule | Get-NetFirewallInterfaceTypeFilter
 $security=$Rule | Get-NetFirewallSecurityFilter
 $signature=[ordered]@{name=$Rule.Name;display=$Rule.DisplayName;description=$Rule.Description;group=$Rule.Group;profile=[string]$Rule.Profile;direction=[string]$Rule.Direction;action=[string]$Rule.Action;
  edge=[string]$Rule.EdgeTraversalPolicy;loose=[string]$Rule.LooseSourceMapping;localOnly=[string]$Rule.LocalOnlyMapping;
  program=$app.Program;package=$app.Package;protocol=[string]$port.Protocol;localPort=@($port.LocalPort);remotePort=@($port.RemotePort);icmp=@($port.IcmpType);
  localAddress=@($address.LocalAddress);remoteAddress=@($address.RemoteAddress);service=$service.Service;interface=@($interface.InterfaceAlias);interfaceType=[string]$type.InterfaceType;
  authentication=[string]$security.Authentication;encryption=[string]$security.Encryption;override=[string]$security.OverrideBlockRules;localUser=$security.LocalUser;remoteUser=$security.RemoteUser;remoteMachine=$security.RemoteMachine}
 if($IncludeEnabled){$signature['enabled']=[string]$Rule.Enabled}
 $signature | ConvertTo-Json -Compress -Depth 8
}
function Assert-KkOriginal($Row){
 $rule=Get-KkFirewallRule $Row.name
 if(-not $rule -or (Get-KkRuleSignature $rule) -cne $Row.signature){throw ('Original rule changed externally: '+$Row.name)}
 $rule
}
function Assert-KkOwned($Name,$Journal){
 $rule=Get-KkFirewallRule $Name
 if(-not $rule -or $rule.Description -ne ('KKNativeTxn:'+$Journal.id)){throw ('Unowned firewall rule: '+$Name)}
 $rule
}
function Assert-KkAllow($Row,$Journal){
 $rule=Assert-KkOwned $Row.name $Journal
 $saved=@($Journal.owned | Where-Object name -eq $Row.name)
 if($saved.Count){if((Get-KkRuleSignature $rule -IncludeEnabled) -cne $saved[0].signature){throw 'Owned rule changed externally'}}
 else{
  $app=$rule | Get-NetFirewallApplicationFilter;$port=$rule | Get-NetFirewallPortFilter;$addr=$rule | Get-NetFirewallAddressFilter
  if($rule.Group -ne $group -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Outbound' -or $rule.Profile -ne 'Any' -or $app.Program -ne $Row.program -or [string]$port.Protocol -ne $Row.protocol -or [string]$port.RemotePort -ne [string]$Row.port -or [string]$addr.RemoteAddress -ne $Row.address){throw 'Interrupted rule creation conflicts with plan'}
 }
 $rule
}
function Write-KkJournal($Journal){
 $data=[Text.UTF8Encoding]::new($false).GetBytes(($Journal | ConvertTo-Json -Depth 20))
 if($data.Length -gt 4194304){throw 'Policy journal too large'}
 $temporary=$receiptPath+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
 try{
  $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$stream.Write($data,0,$data.Length);$stream.Flush($true)}finally{$stream.Dispose()}
  $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetAccessRuleProtection($true,$false)
  foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))}
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'Read','Allow'))
  Set-Acl -LiteralPath $temporary -AclObject $acl
  if(Test-Path -LiteralPath $receiptPath){[IO.File]::Replace($temporary,$receiptPath,$null)}else{[IO.File]::Move($temporary,$receiptPath)}
 }finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Force}}
}
function Assert-KkGuard($Journal){
 $rule=Assert-KkOwned $guardName $Journal
 $active=Get-NetFirewallRule -PolicyStore ActiveStore -Name $guardName -ErrorAction Stop
 $app=$active | Get-NetFirewallApplicationFilter;$addr=$active | Get-NetFirewallAddressFilter;$port=$active | Get-NetFirewallPortFilter
 if($active.Enabled -ne 'True' -or $active.Action -ne 'Block' -or $active.Direction -ne 'Outbound' -or $active.Profile -ne 'Any' -or $app.Program -ne 'Any' -or [string]$addr.RemoteAddress -ne 'Any' -or [string]$port.Protocol -ne 'Any'){throw 'Emergency guard is not effective'}
 $service=$active | Get-NetFirewallServiceFilter;$iface=$active | Get-NetFirewallInterfaceFilter;$type=$active | Get-NetFirewallInterfaceTypeFilter
 if([string]$service.Service -ne 'Any' -or [string]$iface.InterfaceAlias -ne 'Any' -or [string]$type.InterfaceType -ne 'Any' -or [string]$addr.LocalAddress -ne 'Any' -or [string]$port.RemotePort -ne 'Any' -or [string]$port.LocalPort -ne 'Any'){throw 'Emergency guard has a narrowed scope'}
 $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
 if($profiles.Count -ne 3 -or @($profiles | Where-Object Enabled -ne 'True').Count){throw 'Firewall profile is disabled'}
 foreach($allow in @(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Outbound -Enabled True -Action Allow)){
  $security=$allow | Get-NetFirewallSecurityFilter
  if($security.OverrideBlockRules -eq 'True'){throw 'Authenticated bypass conflicts with emergency guard'}
 }
}
function Invoke-KkWindowsBackend($Operation,$Argument){
 switch($Operation){
  'Load' {if(Test-Path -LiteralPath $receiptPath){if((Get-Item -LiteralPath $receiptPath).Length -gt 4194304){throw 'Policy receipt too large'};Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json};return}
  'Save' {Write-KkJournal $Argument;return}
  'Snapshot' {
   if(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue){throw 'Existing native rules without matching journal'}
   if(Get-KkFirewallRule $guardName){throw 'Unowned emergency guard'}
   $rows=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Enabled True -Action Allow | ForEach-Object {[pscustomobject]@{name=$_.Name;signature=(Get-KkRuleSignature $_)}})
   $profiles=@(Get-NetFirewallProfile -PolicyStore PersistentStore | ForEach-Object {[pscustomobject]@{name=[string]$_.Name;enabled=[string]$_.Enabled;outbound=[string]$_.DefaultOutboundAction}})
   [pscustomobject]@{rules=$rows;profiles=$profiles};return
  }
  'EnsureGuard' {
   if(Get-KkFirewallRule $guardName){Assert-KkOwned $guardName $Argument | Out-Null}
   else{New-NetFirewallRule -PolicyStore PersistentStore -Name $guardName -DisplayName $guardName -Description ('KKNativeTxn:'+$Argument.id) -Group $group -Profile Any -Direction Outbound -Action Block -Enabled True -Protocol Any -RemoteAddress Any | Out-Null}
   Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True
   Enable-NetFirewallRule -PolicyStore PersistentStore -Name $guardName | Out-Null;return
  }
  'VerifyGuard' {Assert-KkGuard $Argument;return}
  'SetDefaultDeny' {Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block;return}
  'DisableOriginal' {Assert-KkOriginal $Argument.rule | Out-Null;Disable-NetFirewallRule -PolicyStore PersistentStore -Name $Argument.rule.name | Out-Null;return}
  'EnsureAllow' {
   $row=$Argument.rule;$journal=$Argument.journal
   if(Get-KkFirewallRule $row.name){Assert-KkAllow $row $journal | Out-Null}
   else{New-NetFirewallRule -PolicyStore PersistentStore -Name $row.name -DisplayName $row.name -Description ('KKNativeTxn:'+$journal.id) -Group $group -Profile Any -Direction Outbound -Action Allow -Enabled True -Program $row.program -Protocol $row.protocol -RemoteAddress $row.address -RemotePort $row.port | Out-Null}
   Get-KkRuleSignature (Assert-KkAllow $row $journal) -IncludeEnabled;return
  }
  'VerifyPolicy' {Assert-Policy $Argument.plan;foreach($row in $Argument.plan){Assert-KkAllow $row $Argument | Out-Null};return}
  'RemoveGuard' {if(Get-KkFirewallRule $guardName){Assert-KkOwned $guardName $Argument | Out-Null;Remove-NetFirewallRule -PolicyStore PersistentStore -Name $guardName};return}
  'VerifyComplete' {if(Get-KkFirewallRule $guardName){throw 'Emergency guard still present'};Invoke-KkWindowsBackend 'VerifyPolicy' $Argument;return}
  'CheckRestoreConflicts' {
   foreach($row in $Argument.original.rules){Assert-KkOriginal $row | Out-Null}
   foreach($row in $Argument.plan){if(Get-KkFirewallRule $row.name){Assert-KkAllow $row $Argument | Out-Null}}
   $names=@($Argument.original.rules.name)+@($Argument.plan.name)
   foreach($allow in @(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Outbound -Enabled True -Action Allow)){if($names -notcontains $allow.Name){throw 'External allow rule added during transaction'}}
   foreach($profile in @(Get-NetFirewallProfile -PolicyStore PersistentStore)){
    $old=@($Argument.original.profiles | Where-Object name -eq ([string]$profile.Name))
    if($old.Count -ne 1 -or [string]$profile.DefaultOutboundAction -notin @('Block',$old[0].outbound)){throw 'External profile modification'}
   };return
  }
  'RemoveAllow' {if(Get-KkFirewallRule $Argument.rule.name){Assert-KkAllow $Argument.rule $Argument.journal | Out-Null;Remove-NetFirewallRule -PolicyStore PersistentStore -Name $Argument.rule.name};return}
  'RestoreOriginal' {Assert-KkOriginal $Argument.rule | Out-Null;Enable-NetFirewallRule -PolicyStore PersistentStore -Name $Argument.rule.name | Out-Null;return}
  'RestoreDefaults' {foreach($p in $Argument.original.profiles){Set-NetFirewallProfile -Profile $p.name -DefaultOutboundAction $p.outbound};return}
  'RestoreEnabled' {foreach($p in $Argument.original.profiles){Set-NetFirewallProfile -Profile $p.name -Enabled $p.enabled};return}
  'VerifyRestored' {
   if(Get-KkFirewallRule $guardName){throw 'Guard remains after Restore'}
   foreach($row in $Argument.plan){if(Get-KkFirewallRule $row.name){throw 'Owned allow rule remains'}}
   foreach($row in $Argument.original.rules){$actual=Assert-KkOriginal $row;if($actual.Enabled -ne 'True'){throw 'Original rule not restored'}}
   foreach($p in $Argument.original.profiles){$actual=Get-NetFirewallProfile -PolicyStore PersistentStore -Name $p.name;if([string]$actual.Enabled -ne $p.enabled -or [string]$actual.DefaultOutboundAction -ne $p.outbound){throw 'Original profile not restored'}};return
  }
  default {throw ('Unknown backend operation: '+$Operation)}
 }
}
