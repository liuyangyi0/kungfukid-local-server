// Own-client controller. Credentials travel in process memory, never command
// lines, logs or ticket files. No game socket listener/forwarder exists here.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Xml;

namespace KKLocalAccounts {
public sealed class NativeCloudOptions {
 public string auth_host,adapter_dll,injector_path,initializer_dll,sdk_host,egress_policy_script;
 public int auth_port=17999,sdk_port=18000,game_port=18001,udp_port=18001;
 public bool development_only;
 public void Validate(){
  IPAddress ip;
  if(!development_only||String.IsNullOrWhiteSpace(auth_host)||auth_host.IndexOfAny(new[]{'\r','\n','/','\\','\0'})>=0||auth_port<1||auth_port>65535||sdk_port<1||sdk_port>65535||!IPAddress.TryParse(sdk_host,out ip)||ip.AddressFamily!=AddressFamily.InterNetwork)throw new InvalidOperationException("invalid_native_settings");
  // Main server is deliberately loopback-only until original-client/security
  // qualification. Do not quietly turn this launcher into a public release.
  if(!IPAddress.IsLoopback(ip))throw new InvalidOperationException("native_public_not_ready");
  if(game_port<1||game_port>65535||udp_port<1||udp_port>65535||game_port==sdk_port||auth_port==game_port||auth_port==sdk_port)throw new InvalidOperationException("invalid_native_settings");
  foreach(string path in new[]{adapter_dll,injector_path,initializer_dll,egress_policy_script})if(String.IsNullOrEmpty(path)||!Path.IsPathRooted(path)||!File.Exists(path))throw new InvalidOperationException("missing_native_tools");
 }
}

public static class NativeCloudRpc {
 static byte[] Read(Stream s,int n){var b=new byte[n];for(int at=0;at<n;){int got=s.Read(b,at,n-at);if(got==0)throw new IOException();at+=got;}return b;}
 public static object Call(string host,int port,string operation,Dictionary<string,object> arguments,string address=null){
  var json=new JavaScriptSerializer();byte[] bytes=Encoding.UTF8.GetBytes(json.Serialize(new{schema="kk-local-auth-v1",operation=operation,arguments=arguments}));
  try{using(var tcp=new TcpClient()){
   if(!tcp.ConnectAsync(address??host,port).Wait(5000))throw new IOException();
   using(var tls=new SslStream(tcp.GetStream(),false)){
    tls.ReadTimeout=20000;tls.WriteTimeout=5000;
    // Platform chain/name validation: no accept-all certificate callback.
    tls.AuthenticateAsClient(host,null,SslProtocols.Tls12,true);
    tls.Write(new[]{(byte)(bytes.Length>>24),(byte)(bytes.Length>>16),(byte)(bytes.Length>>8),(byte)bytes.Length},0,4);tls.Write(bytes,0,bytes.Length);tls.Flush();
    var h=Read(tls,4);int n=(h[0]<<24)|(h[1]<<16)|(h[2]<<8)|h[3];if(n<2||n>65536)throw new IOException();
    var response=(Dictionary<string,object>)json.DeserializeObject(Encoding.UTF8.GetString(Read(tls,n)));
    if(!(bool)response["ok"])throw new InvalidOperationException((string)response["error"]);return response["result"];
   }
  }}finally{Array.Clear(bytes,0,bytes.Length);}
 }
}

public static class NativeCloudHandoff {
 public static string RewriteConfig(string xml,string sdkHost,int sdkPort){
  IPAddress ip;if(!IPAddress.TryParse(sdkHost,out ip)||ip.AddressFamily!=AddressFamily.InterNetwork||sdkPort<1||sdkPort>65535)throw new InvalidOperationException("invalid_native_endpoint");
  if(xml==null||xml.Length>1024*1024||xml.IndexOf("<!DOCTYPE",StringComparison.OrdinalIgnoreCase)>=0)throw new InvalidOperationException("invalid_native_config_xml");
  var document=new XmlDocument();document.XmlResolver=null;document.LoadXml(xml);
  var servers=document.GetElementsByTagName("LoginServer");if(servers.Count<1||servers.Count>32)throw new InvalidOperationException("native_login_config_missing");
  foreach(XmlElement element in servers){element.SetAttribute("Ip",sdkHost);element.SetAttribute("Port",sdkPort.ToString(System.Globalization.CultureInfo.InvariantCulture));}
  foreach(string name in new[]{"UpdateInfo","PrePaidURL","RegisterURL"})foreach(XmlElement element in document.GetElementsByTagName(name)){
   foreach(XmlAttribute attr in element.Attributes)if(attr.Name.Equals("url",StringComparison.OrdinalIgnoreCase))attr.Value="http://127.0.0.1:9/disabled";
  }
  return document.OuterXml;
 }
 public static void CheckEgress(string root,NativeCloudOptions options){
  options.Validate();
  string shell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe");
  string arguments="-NoProfile -NonInteractive -File "+Quote(options.egress_policy_script)+" -Action Check -ClientRoot "+Quote(root)+" -ServerAddress "+Quote(options.sdk_host)+" -AuthPort "+options.auth_port+" -SdkPort "+options.sdk_port+" -GamePort "+options.game_port+" -UdpPort "+options.udp_port;
  var check=new ProcessStartInfo(shell,arguments){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardOutput=true,RedirectStandardError=true};
  using(var probe=Process.Start(check)){
   var output=probe.StandardOutput.ReadToEndAsync();var errors=probe.StandardError.ReadToEndAsync();
   if(!probe.WaitForExit(15000)){probe.Kill();throw new InvalidOperationException("native_egress_check_timeout");}
   if(probe.ExitCode!=0||!output.Result.Contains("KK_NATIVE_EGRESS_READY"))throw new InvalidOperationException("native_egress_policy_required");
  }
 }
 public static Process StartClient(string root,string runRoot,NativeCloudOptions options){
  string sdkHost=options.sdk_host;int sdkPort=options.sdk_port;
  root=Path.GetFullPath(root).TrimEnd('\\');
  if(!root.StartsWith("C:\\KK-Lab\\",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("isolated_client_copy_required");
  // Effective OS policy, not a file receipt or a stale route check. Covers old
  // SDK child processes and HTTP/DNS/IPv6 paths before the client first starts.
  CheckEgress(root,options);
  for(var directory=new DirectoryInfo(root);directory!=null;directory=directory.Parent)if((directory.Attributes&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("linked_client_directory_rejected");
  if((File.GetAttributes(Path.Combine(root,"Data"))&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("linked_client_directory_rejected");
  string exe=Path.Combine(root,"gfxz-lab.exe");QualifyClient(exe);
  foreach(Process old in Process.GetProcessesByName("gfxz-lab"))using(old){if(String.Equals(old.MainModule.FileName,exe,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("client_already_running");}
  string config=Path.Combine(root,"Data\\config.xml");if((File.GetAttributes(config)&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("linked_client_config_rejected");string content=File.ReadAllText(config,Encoding.GetEncoding(936));string updated=RewriteConfig(content,sdkHost,sdkPort);
  File.Copy(config,Path.Combine(runRoot,"previous-config.xml"),false);
  string temporary=config+"."+Guid.NewGuid().ToString("N")+".tmp";
  File.WriteAllText(temporary,updated,Encoding.GetEncoding(936));File.Replace(temporary,config,null);
  return Process.Start(new ProcessStartInfo(exe){WorkingDirectory=root,UseShellExecute=false,WindowStyle=ProcessWindowStyle.Normal});
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct ModuleEntry {
  public uint size,moduleId,pid,globalUsage,processUsage;public IntPtr baseAddress;public uint baseSize;public IntPtr module;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=256)]public string name;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string path;
 }
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr VirtualAllocEx(IntPtr process,IntPtr address,UIntPtr size,uint allocation,uint protect);
 [DllImport("kernel32.dll")]static extern bool VirtualFreeEx(IntPtr process,IntPtr address,UIntPtr size,uint type);
 [DllImport("kernel32.dll")]static extern bool WriteProcessMemory(IntPtr process,IntPtr address,byte[] data,UIntPtr size,out UIntPtr written);
 [DllImport("kernel32.dll")]static extern bool ReadProcessMemory(IntPtr process,IntPtr address,byte[] data,UIntPtr size,out UIntPtr read);
 [DllImport("kernel32.dll")]static extern IntPtr CreateRemoteThread(IntPtr process,IntPtr attributes,UIntPtr stack,IntPtr start,IntPtr argument,uint flags,IntPtr threadId);
 [DllImport("kernel32.dll")]static extern uint WaitForSingleObject(IntPtr handle,uint ms);
 [DllImport("kernel32.dll")]static extern bool GetExitCodeThread(IntPtr thread,out uint result);
 [DllImport("kernel32.dll")]static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
 [DllImport("kernel32.dll")]static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Module32FirstW(IntPtr snapshot,ref ModuleEntry entry);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Module32NextW(IntPtr snapshot,ref ModuleEntry entry);
 public static byte[] Pack(int pid,string account,Dictionary<string,object> grant,string sdkHost,int sdkPort){
  if(pid<=0||account.Length<3||account.Length>20||sdkPort<1||sdkPort>65535)throw new InvalidOperationException("invalid_native_configuration");
  foreach(char ch in account)if(!(ch>='a'&&ch<='z'||ch>='A'&&ch<='Z'||ch>='0'&&ch<='9'))throw new InvalidOperationException("invalid_native_configuration");
  long expiry=Convert.ToInt64(grant["session_expires_at"]);long now=(long)(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds;
  if(expiry<=now||expiry-now>28800)throw new InvalidOperationException("native_session_expired");
  IPAddress address;if(!IPAddress.TryParse(sdkHost,out address)||address.AddressFamily!=AddressFamily.InterNetwork)throw new InvalidOperationException("invalid_native_endpoint");
  if((string)grant["sdk_host"]!=sdkHost||Convert.ToInt32(grant["sdk_port"])!=sdkPort)throw new InvalidOperationException("native_endpoint_mismatch");
  using(var data=new MemoryStream())using(var writer=new BinaryWriter(data)){
   writer.Write((uint)224);writer.Write((uint)3);writer.Write((uint)pid);writer.Write((uint)((expiry-now)*1000));
   byte[] name=new byte[21];Encoding.ASCII.GetBytes(account,0,account.Length,name,0);writer.Write(name);
   byte[] tcp=Hex((string)grant["game_credential"]),sdk=Hex((string)grant["sdk_credential"]),udp=Hex((string)grant["udp_credential"]);
   byte[] transport=Hex((string)grant["transport_key"]);
   if((string)grant["transport"]!="kk-aesgcm-v1"||(string)grant["game_host"]!=sdkHost)throw new InvalidOperationException("encrypted_transport_required");
   string sid=(string)grant["transport_id"];if(sid.Length!=32)throw new InvalidOperationException("invalid_transport_id");byte[] id=Hex(sid+sid);
   int gamePort=Convert.ToInt32(grant["game_port"]),udpPort=Convert.ToInt32(grant["udp_port"]);
   if(gamePort<1||gamePort>65535||udpPort<1||udpPort>65535||gamePort==sdkPort)throw new InvalidOperationException("invalid_native_endpoint");
   try{writer.Write(tcp);writer.Write(Encoding.ASCII.GetBytes((string)grant["udp_credential"]));writer.Write((byte)0);writer.Write(sdk);writer.Write(address.GetAddressBytes());writer.Write((ushort)sdkPort);writer.Write(id,0,16);writer.Write(transport);writer.Write((ushort)gamePort);writer.Write((ushort)udpPort);return data.ToArray();}
   finally{Array.Clear(tcp,0,tcp.Length);Array.Clear(sdk,0,sdk.Length);Array.Clear(udp,0,udp.Length);Array.Clear(transport,0,transport.Length);}
  }
 }
 static byte[] Hex(string s){if(s==null||s.Length!=64)throw new InvalidOperationException("invalid_native_ticket");var b=new byte[32];for(int i=0;i<32;i++){string h=s.Substring(i*2,2);foreach(char c in h)if(!(c>='0'&&c<='9'||c>='a'&&c<='f'))throw new InvalidOperationException("invalid_native_ticket");b[i]=Convert.ToByte(h,16);}return b;}
 static string Quote(string text){if(text.IndexOfAny(new[]{'"','\r','\n','\0'})>=0)throw new InvalidOperationException("invalid_local_path");return "\""+text+"\"";}
 public static void Inject(int pid,string expectedExe,string injector,string dll){
  using(var p=Process.GetProcessById(pid)){
   if(!String.Equals(Path.GetFullPath(p.MainModule.FileName),Path.GetFullPath(expectedExe),StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("client_identity_mismatch");
   var start=new ProcessStartInfo(injector,Quote(Path.GetFullPath(dll))+" --pid "+pid){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden};
   using(var child=Process.Start(start)){if(!child.WaitForExit(15000))throw new InvalidOperationException("native_injection_timeout");if(child.ExitCode!=0)throw new InvalidOperationException("native_injection_failed");}
  }
 }
 static uint Offset(byte[] b,uint rva){int pe=BitConverter.ToInt32(b,60),optional=pe+24;int count=BitConverter.ToUInt16(b,pe+6),table=optional+BitConverter.ToUInt16(b,pe+20);for(int i=0;i<count;i++){int s=table+40*i;uint va=BitConverter.ToUInt32(b,s+12),n=BitConverter.ToUInt32(b,s+16),file=BitConverter.ToUInt32(b,s+20);if(rva>=va&&rva-va<n&&file+(ulong)rva-va<(ulong)b.Length)return file+rva-va;}throw new InvalidOperationException("invalid_adapter_image");}
 public static uint ExportRva(string path,string name){
  var file=new FileInfo(path);if(file.Length<256||file.Length>4*1024*1024)throw new InvalidOperationException("invalid_adapter_image");
  byte[] b=File.ReadAllBytes(path);int pe=BitConverter.ToInt32(b,60);if(pe<64||pe+248>b.Length||BitConverter.ToUInt32(b,pe)!=0x4550||BitConverter.ToUInt16(b,pe+4)!=0x14c||BitConverter.ToUInt16(b,pe+24)!=0x10b)throw new InvalidOperationException("adapter_must_be_i686");
  uint export=BitConverter.ToUInt32(b,pe+120),size=BitConverter.ToUInt32(b,pe+124),off=Offset(b,export);
  uint functions=BitConverter.ToUInt32(b,(int)off+20),count=BitConverter.ToUInt32(b,(int)off+24);if(count>128||functions>128)throw new InvalidOperationException("invalid_adapter_exports");
  uint fs=Offset(b,BitConverter.ToUInt32(b,(int)off+28)),ns=Offset(b,BitConverter.ToUInt32(b,(int)off+32)),os=Offset(b,BitConverter.ToUInt32(b,(int)off+36));
  for(uint i=0;i<count;i++){uint p=Offset(b,BitConverter.ToUInt32(b,(int)(ns+4*i)));int end=Array.IndexOf(b,(byte)0,(int)p,Math.Min(80,b.Length-(int)p));if(end<0)continue;if(Encoding.ASCII.GetString(b,(int)p,end-(int)p)!=name)continue;int index=BitConverter.ToUInt16(b,(int)(os+2*i));if(index>=functions)throw new InvalidOperationException("invalid_adapter_exports");uint rva=BitConverter.ToUInt32(b,(int)fs+4*index);if(rva>=export&&rva-export<size)throw new InvalidOperationException("forwarded_adapter_export");Offset(b,rva);return rva;}
  throw new InvalidOperationException("adapter_export_missing");
 }
 static IntPtr Module(int pid,string path){IntPtr h=CreateToolhelp32Snapshot(0x18,(uint)pid);if(h==new IntPtr(-1))throw new InvalidOperationException("native_modules_unavailable");try{ModuleEntry e=new ModuleEntry();e.size=(uint)Marshal.SizeOf(typeof(ModuleEntry));bool more=Module32FirstW(h,ref e);while(more){if(String.Equals(Path.GetFullPath(e.path),Path.GetFullPath(path),StringComparison.OrdinalIgnoreCase))return e.baseAddress;more=Module32NextW(h,ref e);}throw new InvalidOperationException("adapter_module_not_loaded");}finally{CloseHandle(h);}}
 public static uint Invoke(int pid,string expectedExe,string dll,string export,byte[] data){
  using(var process=Process.GetProcessById(pid)){
   DateTime created=process.StartTime;if(!String.Equals(process.MainModule.FileName,expectedExe,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("client_identity_mismatch");
   IntPtr module=Module(pid,dll);uint rva=ExportRva(dll,export);IntPtr h=OpenProcess(0x43a,false,pid);if(h==IntPtr.Zero)throw new InvalidOperationException("native_process_access_failed");
   IntPtr memory=IntPtr.Zero,thread=IntPtr.Zero;bool completed=false;
   try{long born,ended,kernel,user;if(process.HasExited||!GetProcessTimes(h,out born,out ended,out kernel,out user)||born!=created.ToUniversalTime().ToFileTimeUtc())throw new InvalidOperationException("client_identity_changed");
    if(data!=null){memory=VirtualAllocEx(h,IntPtr.Zero,(UIntPtr)data.Length,0x3000,4);UIntPtr wrote;if(memory==IntPtr.Zero||!WriteProcessMemory(h,memory,data,(UIntPtr)data.Length,out wrote)||wrote.ToUInt64()!=(ulong)data.Length)throw new InvalidOperationException("native_configuration_write_failed");}
    thread=CreateRemoteThread(h,IntPtr.Zero,UIntPtr.Zero,new IntPtr(module.ToInt64()+rva),memory,0,IntPtr.Zero);if(thread==IntPtr.Zero)throw new InvalidOperationException("native_call_failed");
    if(WaitForSingleObject(thread,15000)!=0)throw new InvalidOperationException("native_adapter_timeout");completed=true;
    uint result;if(!GetExitCodeThread(thread,out result))throw new InvalidOperationException("native_call_failed");
    if(data!=null){UIntPtr got;if(!ReadProcessMemory(h,memory,data,(UIntPtr)data.Length,out got)||got.ToUInt64()!=(ulong)data.Length)throw new InvalidOperationException("native_status_read_failed");}
    return result;
   }finally{if(memory!=IntPtr.Zero&&(thread==IntPtr.Zero||completed)){byte[] zeros=new byte[data.Length];UIntPtr wrote;WriteProcessMemory(h,memory,zeros,(UIntPtr)zeros.Length,out wrote);VirtualFreeEx(h,memory,UIntPtr.Zero,0x8000);}if(thread!=IntPtr.Zero)CloseHandle(thread);CloseHandle(h);}
  }
 }
 public static bool RoleReady(int pid,string expectedExe){using(var p=Process.GetProcessById(pid)){if(!String.Equals(p.MainModule.FileName,expectedExe,StringComparison.OrdinalIgnoreCase)||p.MainModule.BaseAddress.ToInt64()!=0x400000)throw new InvalidOperationException("client_identity_mismatch");IntPtr h=OpenProcess(0x410,false,pid);if(h==IntPtr.Zero)throw new InvalidOperationException("native_process_access_failed");try{byte[] b=new byte[4];UIntPtr got;return ReadProcessMemory(h,new IntPtr(0x17C86F0),b,(UIntPtr)4,out got)&&got.ToUInt64()==4&&BitConverter.ToUInt32(b,0)>=0x10000;}finally{CloseHandle(h);}}}
 public static void QualifyClient(string exe){using(var sha=SHA256.Create())using(var f=File.OpenRead(exe)){if(BitConverter.ToString(sha.ComputeHash(f)).Replace("-","")!="1B7C8676E778C7BD47F55184F927338DA3CF70C6AA5F0BEB59869EA0F0AC9D4E")throw new InvalidOperationException("native_client_revision_mismatch");}}
 public static uint Status(int pid,string exe,string dll){var data=new byte[16];uint code=Invoke(pid,exe,dll,"KkNativeCloudGetStatus",data);if(code!=0||BitConverter.ToUInt32(data,0)!=16)throw new InvalidOperationException("native_adapter_status_failed");return BitConverter.ToUInt32(data,4);}
 public static void WaitInstalled(int pid,string exe,string dll,uint mask,int seconds){
  DateTime deadline=DateTime.UtcNow.AddSeconds(seconds);
  do{uint code=Invoke(pid,exe,dll,"KkNativeCloudInstall",null);if(code!=0&&code!=126&&code!=170)throw new InvalidOperationException("native_adapter_install_failed");if((Status(pid,exe,dll)&mask)==mask)return;Thread.Sleep(150);}while(DateTime.UtcNow<deadline);
  throw new InvalidOperationException("native_modules_timeout");
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct ProcessEntry {
  public uint size,usage,pid;public UIntPtr heap;public uint module,threads,parent;public int priority;public uint flags;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string name;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Process32FirstW(IntPtr snapshot,ref ProcessEntry entry);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Process32NextW(IntPtr snapshot,ref ProcessEntry entry);
 delegate bool WindowVisitor(IntPtr window,IntPtr parameter);
 [DllImport("user32.dll")]static extern bool EnumWindows(WindowVisitor visitor,IntPtr parameter);
 [DllImport("user32.dll")]static extern bool EnumChildWindows(IntPtr parent,WindowVisitor visitor,IntPtr parameter);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 [DllImport("user32.dll")]static extern bool ShowWindow(IntPtr window,int command);
 public static void HideSdk(int pid,string root){
  using(var game=Process.GetProcessById(pid)){
   if(!String.Equals(game.MainModule.FileName,Path.Combine(root,"gfxz-lab.exe"),StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("client_identity_mismatch");
   IntPtr snapshot=CreateToolhelp32Snapshot(2,0);if(snapshot==new IntPtr(-1))throw new InvalidOperationException("native_modules_unavailable");
   try{ProcessEntry e=new ProcessEntry();e.size=(uint)Marshal.SizeOf(typeof(ProcessEntry));bool more=Process32FirstW(snapshot,ref e);while(more){
    if(e.parent==(uint)pid&&String.Equals(e.name,"sdologin.exe",StringComparison.OrdinalIgnoreCase))using(var sdk=Process.GetProcessById((int)e.pid)){
     IntPtr keep=sdk.Handle;
     if(String.Equals(sdk.MainModule.FileName,Path.Combine(root,"sdo\\sdologin\\sdologin.exe"),StringComparison.OrdinalIgnoreCase)&&sdk.StartTime>=game.StartTime){
      uint target=(uint)sdk.Id;WindowVisitor hide=delegate(IntPtr w,IntPtr p){uint owner;GetWindowThreadProcessId(w,out owner);if(owner==target)ShowWindow(w,0);return true;};
      EnumWindows(delegate(IntPtr w,IntPtr p){hide(w,p);EnumChildWindows(w,hide,p);return true;},IntPtr.Zero);
     }
    }
    more=Process32NextW(snapshot,ref e);
   }}finally{CloseHandle(snapshot);}
  }
 }
}
}
