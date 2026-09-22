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
 public volatile int RegionMode=0;public volatile bool Stop;public int Calls;public int Port;
 public KkAccountApiFixture(){listener.Start();Port=((IPEndPoint)listener.LocalEndpoint).Port;thread=new Thread(Run);thread.IsBackground=true;thread.Start();}
 static byte[] Read(Stream s,int n){var b=new byte[n];int p=0;while(p<n){int k=s.Read(b,p,n-p);if(k==0)throw new IOException();p+=k;}return b;}
 void Run(){while(!Stop){try{using(var client=listener.AcceptTcpClient()){client.ReceiveTimeout=3000;client.SendTimeout=3000;using(var s=client.GetStream()){
 var h=Read(s,4);int n=(h[0]<<24)|(h[1]<<16)|(h[2]<<8)|h[3];if(n<2||n>8192)throw new IOException();
 var json=new JavaScriptSerializer();var request=(Dictionary<string,object>)json.DeserializeObject(Encoding.UTF8.GetString(Read(s,n)));Interlocked.Increment(ref Calls);string op=(string)request["operation"];object reply;
 if(op=="login")reply=new {ok=true,result=new {uid=1000,session=new String('0',64)}};
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
    'PASS: login/register mode, account/password/nickname validation, loopback API region failure/retry/empty/expired states, no duplicate launch, busy/idle close, and secret-free progress receipt. No real server, database, SDK or game was started.'
} finally {
    $window.Dispose()
    $fixture.Dispose()
    # Exact, uniquely generated test file; never a client or user artifact.
    if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}
}
