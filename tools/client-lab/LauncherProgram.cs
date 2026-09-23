using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("功夫小子启动器")]
[assembly: AssemblyDescription("账号登录、服务器设置与原客户端交接")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace KKLocalAccounts {
public static class LauncherBootstrap {
 public static NativeCloudOptions LoadOptions(string settingsPath){
  settingsPath=Path.GetFullPath(settingsPath);
  if(!File.Exists(settingsPath))throw new InvalidOperationException("launcher_settings_missing");
  if(new FileInfo(settingsPath).Length>65536)throw new InvalidOperationException("invalid_native_settings");
  var settings=new JavaScriptSerializer().Deserialize<NativeCloudOptions>(File.ReadAllText(settingsPath));
  if(settings==null)throw new InvalidOperationException("invalid_native_settings");
  string root=Path.GetDirectoryName(settingsPath);
  foreach(string field in new[]{"adapter_dll","injector_path","initializer_dll","certificate_path","initializer_log"}){
   var info=typeof(NativeCloudOptions).GetField(field);var path=(string)info.GetValue(settings);
   if(!String.IsNullOrWhiteSpace(path))info.SetValue(settings,Path.GetFullPath(Path.IsPathRooted(path)?path:Path.Combine(root,path)));
  }
  settings.Validate();return settings;
 }
 public static string InstanceName(string clientRoot){
  using(var sha=SHA256.Create())return "Local\\KKLauncher-"+BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(clientRoot).TrimEnd('\\').ToUpperInvariant()))).Replace("-","");
 }
}
internal static class LauncherProgram {
 [STAThread]
 static int Main(string[] args){
  try{
   string root=AppDomain.CurrentDomain.BaseDirectory,settings=null,data=null;
   for(int i=0;i<args.Length;i+=2){
    if(i+1>=args.Length)throw new InvalidOperationException("invalid_launcher_arguments");
    switch(args[i]){case "--client-root":root=args[i+1];break;case "--settings":settings=args[i+1];break;case "--data-root":data=args[i+1];break;default:throw new InvalidOperationException("invalid_launcher_arguments");}
   }
   root=Path.GetFullPath(root);
   if(!File.Exists(Path.Combine(root,"gfxz-lab.exe")))throw new InvalidOperationException("launcher_client_missing");
   settings=String.IsNullOrWhiteSpace(settings)?Path.Combine(root,"launcher","launcher.json"):Path.GetFullPath(settings);
   data=String.IsNullOrWhiteSpace(data)?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"KungFuKid","Launcher"):Path.GetFullPath(data);
   using(var mutex=new Mutex(false,LauncherBootstrap.InstanceName(root))){
    bool acquired;try{acquired=mutex.WaitOne(0);}catch(AbandonedMutexException){acquired=true;}
    if(!acquired){MessageBox.Show("登录窗口已经打开，请使用现有窗口。","功夫小子",MessageBoxButtons.OK,MessageBoxIcon.Information);return 0;}
    try{var options=LauncherBootstrap.LoadOptions(settings);Directory.CreateDirectory(data);AccountWindow.RunNative(options,root,data,"");}
    finally{mutex.ReleaseMutex();}
   }
   return 0;
  }catch(Exception error){
   string message="启动器无法启动，请检查 launcher 配套文件及目录权限。";
   if(error is InvalidOperationException){switch(error.Message){
    case "launcher_client_missing":message="请把启动器放在已适配的游戏目录中，与 gfxz-lab.exe 同级。";break;
    case "launcher_settings_missing":message="缺少 launcher\\launcher.json。启动器需要与配套 launcher 文件夹一起安装。";break;
    case "missing_native_tools":message="游戏兼容组件缺失，请修复 launcher 文件夹。";break;
    case "missing_server_certificate":message="配置中的服务器证书文件不存在，请检查证书加载路径。";break;
    case "invalid_native_settings":message="服务器配置无效，请检查地址和端口。";break;
    case "invalid_launcher_arguments":message="启动参数无效。通常双击启动器即可，不需要额外参数。";break;
   }}
   MessageBox.Show(message,"功夫小子启动器",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;
  }
 }
}
}
