[CmdletBinding()]
param([string]$PreviewDirectory='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing,System.Web.Extensions
$source=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'LocalAccountWindow.cs') -Raw -Encoding UTF8
Add-Type -Path @((Join-Path $PSScriptRoot 'LocalAccountWindow.cs'),(Join-Path $PSScriptRoot 'NativeCloudHandoff.cs')) -ReferencedAssemblies System,System.Core,System.Windows.Forms,System.Drawing,System.Web.Extensions,System.Xml
Add-Type -ReferencedAssemblies System,System.Core,System.Web.Extensions -TypeDefinition @'
using System;using System.IO;using System.Net;using System.Net.Sockets;using System.Text;using System.Threading;using System.Web.Script.Serialization;using System.Collections.Generic;
public sealed class KkAccountApiFixture : IDisposable {
 readonly TcpListener listener=new TcpListener(IPAddress.Loopback,0);readonly Thread thread;
 public volatile int RegionMode=0;public volatile bool RejectLogin=false;public volatile bool Stop;public int Calls;public int Port;
 public KkAccountApiFixture(){listener.Start();Port=((IPEndPoint)listener.LocalEndpoint).Port;thread=new Thread(Run);thread.IsBackground=true;thread.Start();}
 static byte[] Read(Stream s,int n){var b=new byte[n];int p=0;while(p<n){int k=s.Read(b,p,n-p);if(k==0)throw new IOException();p+=k;}return b;}
 void Run(){while(!Stop){try{using(var client=listener.AcceptTcpClient()){client.ReceiveTimeout=3000;client.SendTimeout=3000;using(var s=client.GetStream()){
 var h=Read(s,4);int n=(h[0]<<24)|(h[1]<<16)|(h[2]<<8)|h[3];if(n<2||n>8192)throw new IOException();
 var json=new JavaScriptSerializer();var request=(Dictionary<string,object>)json.DeserializeObject(Encoding.UTF8.GetString(Read(s,n)));Interlocked.Increment(ref Calls);string op=(string)request["operation"];object reply;
 if(op=="login"&&RejectLogin)reply=new {ok=false,error="invalid_credentials"};
 else if(op=="login")reply=new {ok=true,result=new {uid=1000,session=new String('0',64)}};
 else if(op=="regions"&&RegionMode==0)reply=new {ok=false,error="service_unavailable"};
 else if(op=="regions"&&RegionMode==2)reply=new {ok=false,error="invalid_session"};
 else if(op=="regions"&&RegionMode==3)reply=new {ok=true,result=new object[0]};
 else if(op=="regions")reply=new {ok=true,result=new[]{new {id=1,name="本地测试区"}}};
 else reply=new {ok=false,error="fixture_unexpected_operation"};
 byte[] b=Encoding.UTF8.GetBytes(json.Serialize(reply));s.Write(new[]{(byte)(b.Length>>24),(byte)(b.Length>>16),(byte)(b.Length>>8),(byte)b.Length},0,4);s.Write(b,0,b.Length);
 }}}catch(SocketException){if(!Stop)throw;}catch(IOException){if(!Stop)throw;}}}
 public void Dispose(){Stop=true;listener.Stop();if(!thread.Join(4000))throw new Exception("Fixture worker did not stop");}
}
'@
$type=[KKLocalAccounts.AccountWindow]
$private=[Reflection.BindingFlags]'Instance,NonPublic'
$fixture=[KkAccountApiFixture]::new()
$window=[Activator]::CreateInstance($type,$private,$null,@($fixture.Port,'C:\KK-Test','C:\KK-Test','C:\KK-Test\events.jsonl'),$null)
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('kk-window-test-'+[Guid]::NewGuid().ToString('N')+'.json')
function Field($name){$type.GetField($name,$private).GetValue($window)}
function Invoke-Window($name,[object[]]$values=@()){$type.GetMethod($name,$private).Invoke($window,$values)}
function Wait-WindowIdle {
 $limit=[DateTime]::UtcNow.AddSeconds(8)
 do{[Windows.Forms.Application]::DoEvents();if(-not (Field 'busy')){return};[Threading.Thread]::Sleep(10)}while([DateTime]::UtcNow -lt $limit)
 throw 'Window worker did not complete'
}
function Preview($name){
 if(-not $PreviewDirectory){return}
 $window.PerformLayout();$bitmap=[Drawing.Bitmap]::new($window.Width,$window.Height)
 try{$window.DrawToBitmap($bitmap,[Drawing.Rectangle]::new(0,0,$window.Width,$window.Height));$path=Join-Path $PreviewDirectory ($name+'.png');if(Test-Path -LiteralPath $path){throw 'Preview exists'};$bitmap.Save($path,[Drawing.Imaging.ImageFormat]::Png)}finally{$bitmap.Dispose()}
}
try {
    if($PreviewDirectory){New-Item -ItemType Directory -Path $PreviewDirectory -ErrorAction Stop|Out-Null}
    # Render our own form off-screen, never manipulate a user window or the VM.
    $window.ShowInTaskbar=$false
    $window.StartPosition=[Windows.Forms.FormStartPosition]::Manual
    $window.Location=[Drawing.Point]::new(-32000,-32000)
    $window.Show()
    [Windows.Forms.Application]::DoEvents()
    if($window.AcceptButton -ne (Field 'login')){throw 'Login must be the default Enter action'}
    Preview 'login'
    $fixture.RejectLogin=$true;(Field 'account').Text='testuser';(Field 'password').Text='synthetic-wrong-password'
    Invoke-Window 'Login';Wait-WindowIdle
    $feedback=Field 'status';$center=[Drawing.Point]::new($feedback.Left+10,$feedback.Top+10)
    if((Field 'session')-or(Field 'busy')-or-not$feedback.Text.Contains('账号或密码不正确')){throw 'Rejected login did not show a retryable error'}
    if($window.GetChildAtPoint($center)-ne$feedback-or$feedback.Bounds.IntersectsWith((Field 'credentialsPanel').Bounds)){throw 'Login error is covered by another control'}
    if(-not(Field 'login').Enabled-or(Field 'password').Text){throw 'Rejected login retained password or disabled retry'}
    $popup=Field 'authenticationError'
    if(-not$popup-or-not$popup.Visible-or$popup.Owner-ne$window-or$popup.Text-ne'登录失败'){throw 'Rejected login did not open an owned error dialog'}
    Invoke-Window 'ShowError' @('invalid_credentials')
    if((Field 'authenticationError')-ne$popup-or$window.OwnedForms.Count-ne1){throw 'Duplicate authentication error dialogs'}
    $popup.AcceptButton.PerformClick();[Windows.Forms.Application]::DoEvents()
    if((Field 'authenticationError')-or-not$feedback.Text.Contains('账号或密码不正确')){throw 'Dismissed error lost persistent feedback'}
    Preview 'login-error'
    $fixture.RejectLogin=$false
    Invoke-Window 'SetMode' @($true)
    if($window.AcceptButton -ne (Field 'register') -or (Field 'fields').RowStyles[2].Height -eq 0){throw 'Registration mode failed'}
    (Field 'account').Text='bad!';(Field 'password').Text='synthetic-password'
    if(Invoke-Window 'ValidateFields' @($false)){throw 'Invalid account accepted'}
    (Field 'account').Text='testuser';(Field 'confirmation').Text='different'
    if(Invoke-Window 'ValidateFields' @($true)){throw 'Mismatched confirmation accepted'}
    (Field 'confirmation').Text='synthetic-password';(Field 'nickname').Text=[Char]::ConvertFromUtf32(0x1F600)
    if(Invoke-Window 'ValidateFields' @($true)){throw 'Unsupported nickname accepted'}
    (Field 'nickname').Text='测试昵称'
    if(-not (Invoke-Window 'ValidateFields' @($true))){throw 'Valid registration fields rejected'}
    $static=[Reflection.BindingFlags]'Static,NonPublic'
    $validate=$type.GetMethod('ValidatePassword',$static)
    $unicodePassword=([Char]::ConvertFromUtf32(0x1F600))*12
    if($null -ne $validate.Invoke($null,@($unicodePassword)) -or $null -eq $validate.Invoke($null,@('short'))){throw 'Password scalar count differs from server'}
    (Field 'password').Clear();(Field 'confirmation').Clear();(Field 'nickname').Clear();(Field 'account').Clear()
    Invoke-Window 'SetMode' @($true)
    Preview 'register'
    Invoke-Window 'SetMode' @($false)
    (Field 'account').Text='testuser';(Field 'password').Text='synthetic-password'
    Invoke-Window 'Login';Wait-WindowIdle
    if(-not (Field 'session') -or -not (Field 'refresh').Enabled -or (Field 'password').Text){throw 'Region failure lost session or retained password'}
    $fixture.RegionMode=1;Invoke-Window 'RefreshRegions';Wait-WindowIdle
    if(-not (Field 'enter').Enabled -or (Field 'regions').Items.Count -ne 1 -or $window.AcceptButton -ne (Field 'enter')){throw 'Region retry not usable'}
    Preview 'regions'
    $type.GetField('entered',$private).SetValue($window,$true);Invoke-Window 'SetBusy' @($false)
    $calls=$fixture.Calls;Invoke-Window 'EnterGame'
    if((Field 'enter').Enabled -or $fixture.Calls -ne $calls){throw 'Completed entry allowed another launch'}
    $type.GetField('entered',$private).SetValue($window,$false)
    $fixture.RegionMode=3;Invoke-Window 'RefreshRegions';Wait-WindowIdle
    if((Field 'enter').Enabled -or -not (Field 'refresh').Enabled){throw 'Empty region list is not retryable'}
    $fixture.RegionMode=2;Invoke-Window 'RefreshRegions';Wait-WindowIdle
    if((Field 'session') -or -not (Field 'login').Enabled -or (Field 'regions').Items.Count){throw 'Expired session cannot log in again'}
    $busy=$type.GetField('busy',$private)
    $closing=$type.GetMethod('OnFormClosing',$private)
    $busy.SetValue($window,$true)
    $event=[Windows.Forms.FormClosingEventArgs]::new([Windows.Forms.CloseReason]::UserClosing,$false)
    $closing.Invoke($window,@($event)) | Out-Null
    if(-not $event.Cancel -or $window.IsDisposed){throw 'Busy window abandoned the entry worker'}
    $busy.SetValue($window,$false)
    $event=[Windows.Forms.FormClosingEventArgs]::new([Windows.Forms.CloseReason]::UserClosing,$false)
    $closing.Invoke($window,@($event)) | Out-Null
    if($event.Cancel){throw 'Idle window cannot close'}
    $type.GetField('entryReceipt',$private).SetValue($window,$temporary)
    $stage=$type.GetMethod('EntryStage',$private)
    $stage.Invoke($window,@('waiting_lobby',$null)) | Out-Null
    $stage.Invoke($window,@('completed',$null)) | Out-Null
    $record=Get-Content -LiteralPath $temporary -Raw | ConvertFrom-Json
    if($record.phase -ne 'completed' -or @($record.PSObject.Properties).Count -ne 4){throw 'Unexpected progress receipt'}
    # The guest tester is Windows PowerShell 5.1: $null binds as an empty
    # backup filename, so cover its actual atomic-write overload separately.
    [IO.File]::WriteAllText(($temporary+'.tmp'),'receipt-replaced')
    [IO.File]::Replace(($temporary+'.tmp'),$temporary,[System.Management.Automation.Language.NullString]::Value)
    if([IO.File]::ReadAllText($temporary)-ne'receipt-replaced'){throw 'PowerShell atomic receipt replacement failed'}
    $options=[KKLocalAccounts.NativeCloudOptions]::new()
    $options.auth_host='127.0.0.1';$options.sdk_host='127.0.0.1'
    $options.adapter_dll=$options.injector_path=$options.initializer_dll=$PSCommandPath
    [KKLocalAccounts.LauncherSettings]::Save($temporary,$options)
    $preferences=Get-Content -LiteralPath $temporary -Raw|ConvertFrom-Json
    if(@($preferences.PSObject.Properties).Count-ne7 -or $preferences.PSObject.Properties['adapter_dll']){throw 'Preferences persist tool configuration or secrets'}
    $copy=[KKLocalAccounts.LauncherSettings]::Load($temporary,$options)
    if($copy.sdk_host-ne'127.0.0.1'-or$copy.adapter_dll-ne$PSCommandPath){throw 'Endpoint preference roundtrip failed'}
    $copy.sdk_host='not-an-ip';$rejected=$false
    try{[KKLocalAccounts.LauncherSettings]::Save($temporary,$copy)}catch{$rejected=$true}
    if(-not$rejected-or([KKLocalAccounts.LauncherSettings]::Load($temporary,$options)).sdk_host-ne'127.0.0.1'){throw 'Invalid endpoint overwrote valid settings'}
    $copy=[KKLocalAccounts.LauncherSettings]::Copy($options);$copy.auth_port=$copy.sdk_port;$rejected=$false
    try{$copy.ValidateEndpoint()}catch{$rejected=$true};if(-not$rejected){throw 'Conflicting ports accepted'}
    [IO.File]::WriteAllText($temporary,' loaded pid=100 roleprop_ready table=0x1234')
    if(-not[KKLocalAccounts.LauncherSettings]::FreshInitializerReady($temporary,0,100)-or[KKLocalAccounts.LauncherSettings]::FreshInitializerReady($temporary,0,101)){throw 'Initializer PID scope failed'}
    $length=(Get-Item -LiteralPath $temporary).Length
    if([KKLocalAccounts.LauncherSettings]::FreshInitializerReady($temporary,$length,100)){throw 'Old initializer marker accepted'}
    [IO.File]::AppendAllText($temporary,' loaded pid=101 roleprop_ready table=0x5678')
    if([KKLocalAccounts.LauncherSettings]::FreshInitializerReady($temporary,0,100)){throw 'Mixed-process initializer log accepted'}
    $type.GetField('entryReceipt',$private).SetValue($window,$null)
    $window.ConfigureNative($options);$window.WindowState=[Windows.Forms.FormWindowState]::Normal
    if(-not(Field 'serverSettings').Visible){throw 'Server settings button missing'}
    Invoke-Window 'SetBusy' @($true);if((Field 'serverSettings').Enabled){throw 'Server can change during handoff'};Invoke-Window 'SetBusy' @($false)
    Preview 'native-login'
    Invoke-Window 'SetMode' @($true);if(-not(Field 'invitation').Visible){throw 'Public registration invitation field missing'}
    Preview 'native-register'
    $dialog=[KKLocalAccounts.ServerSettingsWindow]::new($options)
    try{
      $dialog.ShowInTaskbar=$false;$dialog.StartPosition=[Windows.Forms.FormStartPosition]::Manual;$dialog.Location=[Drawing.Point]::new(-32000,-32000);$dialog.Show();[Windows.Forms.Application]::DoEvents()
      if($PreviewDirectory){$bmp=[Drawing.Bitmap]::new($dialog.Width,$dialog.Height);try{$dialog.DrawToBitmap($bmp,[Drawing.Rectangle]::new(0,0,$dialog.Width,$dialog.Height));$bmp.Save((Join-Path $PreviewDirectory 'server-settings.png'),[Drawing.Imaging.ImageFormat]::Png)}finally{$bmp.Dispose()}}
      $dialogType=$dialog.GetType();$dialogType.GetField('address',$private).GetValue($dialog).Text='192.0.2.5'
      $dialogType.GetField('tlsName',$private).GetValue($dialog).Text='game.example.test'
      $dialog.AcceptButton.PerformClick()
      if($dialog.DialogResult-ne[Windows.Forms.DialogResult]::OK-or$dialog.Result.sdk_host-ne'192.0.2.5'-or$dialog.Result.auth_host-ne'game.example.test'){throw 'Settings Save button did not apply endpoint'}
    }finally{$dialog.Dispose()}
    'PASS: endpoint persistence/validation, no secret or tool override, settings busy lock, invitation layout, PID-scoped fresh initializer readiness.'
    'PASS: login/register mode, account/password/nickname validation, loopback API region failure/retry/empty/expired states, no duplicate launch, busy/idle close, and secret-free progress receipt. No real server, database, SDK or game was started.'
} finally {
    $window.Dispose()
    $fixture.Dispose()
    # Exact, uniquely generated test file; never a client or user artifact.
    if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}
}
