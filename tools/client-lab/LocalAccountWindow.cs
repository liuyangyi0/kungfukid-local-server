using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace KKLocalAccounts {
public sealed class ServerSettingsWindow : Form {
 public NativeCloudOptions Result {get;private set;}
 readonly TextBox address=new TextBox(),tlsName=new TextBox();
 readonly NumericUpDown auth=new NumericUpDown(),sdk=new NumericUpDown(),game=new NumericUpDown(),udp=new NumericUpDown();
 public ServerSettingsWindow(NativeCloudOptions current){
  Text="服务器设置";ClientSize=new Size(420,390);FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=MinimizeBox=false;StartPosition=FormStartPosition.CenterParent;
  Font=new Font("Microsoft YaHei UI",10);BackColor=Color.FromArgb(24,28,34);ForeColor=Color.Gainsboro;
  Controls.Add(new Label{Text="连接你选择的服务器",Location=new Point(24,22),AutoSize=true,Font=new Font(Font.FontFamily,16,FontStyle.Bold)});
  Add("服务器 IP",address,70);address.Text=current.sdk_host;
  Add("证书名称",tlsName,114);tlsName.Text=current.auth_host;
  address.TextChanged+=delegate{if(tlsName.Text==current.sdk_host||tlsName.Text==current.auth_host&&current.auth_host==current.sdk_host)tlsName.Text=address.Text;};
  // Default: the certificate name follows the address. Separate name supports
  // deployments whose certificate identifies a DNS name rather than the IP.
  address.Leave+=delegate{if(current.auth_host==current.sdk_host)tlsName.Text=address.Text.Trim();};
  AddPort("账号端口",auth,current.auth_port,162);AddPort("身份端口",sdk,current.sdk_port,204);AddPort("游戏端口",game,current.game_port,246);AddPort("UDP 端口",udp,current.udp_port,288);
  var save=new Button{Text="保存",Location=new Point(270,338),Size=new Size(124,34),BackColor=Color.FromArgb(229,187,112),ForeColor=Color.FromArgb(35,28,18),FlatStyle=FlatStyle.Flat};Controls.Add(save);AcceptButton=save;
  var cancel=new Button{Text="取消",Location=new Point(160,338),Size=new Size(96,34),DialogResult=DialogResult.Cancel};Controls.Add(cancel);CancelButton=cancel;
  save.Click+=delegate{try{var copy=LauncherSettings.Copy(current);copy.sdk_host=address.Text.Trim();copy.auth_host=tlsName.Text.Trim();copy.auth_port=(int)auth.Value;copy.sdk_port=(int)sdk.Value;copy.game_port=(int)game.Value;copy.udp_port=(int)udp.Value;copy.ValidateEndpoint();Result=copy;DialogResult=DialogResult.OK;}catch(InvalidOperationException){MessageBox.Show(this,"请输入有效 IPv4 地址、证书名称和端口；账号、身份、游戏 TCP 端口不能重复。","检查服务器设置",MessageBoxButtons.OK,MessageBoxIcon.Warning);}};
 }
 void Add(string name,Control input,int top){Controls.Add(new Label{Text=name,Location=new Point(24,top+4),Size=new Size(112,26)});input.SetBounds(140,top,254,30);Controls.Add(input);}
 void AddPort(string name,NumericUpDown input,int value,int top){input.Minimum=1;input.Maximum=65535;input.Value=value;Add(name,input,top);}
}
// Native vector artwork: no external image, proprietary resource, or web fetch.
internal sealed class ThemePanel : Panel {
 public ThemePanel(){DoubleBuffered=true;}
 protected override void OnPaint(PaintEventArgs e){
  base.OnPaint(e);var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;
  using(var gradient=new LinearGradientBrush(ClientRectangle,Color.FromArgb(45,43,39),Color.FromArgb(14,21,28),65f))g.FillRectangle(gradient,ClientRectangle);
  using(var pen=new Pen(Color.FromArgb(26,229,187,112),1)){g.DrawEllipse(pen,81,216,354,354);g.DrawEllipse(pen,94,229,328,328);g.DrawLine(pen,38,113,112,113);}
  using(var path=new GraphicsPath())using(var face=new FontFamily("KaiTi"))using(var ink=new SolidBrush(Color.FromArgb(39,187,163,119))){path.AddString("武",face,(int)FontStyle.Bold,254,new PointF(115,250),StringFormat.GenericDefault);g.FillPath(ink,path);}
  using(var distant=new SolidBrush(Color.FromArgb(21,29,36)))g.FillPolygon(distant,new[]{new Point(0,428),new Point(70,392),new Point(138,442),new Point(250,365),new Point(340,445),new Point(444,399),new Point(444,580),new Point(0,580)});
  using(var near=new SolidBrush(Color.FromArgb(13,20,26)))g.FillPolygon(near,new[]{new Point(0,473),new Point(85,449),new Point(181,487),new Point(310,431),new Point(444,488),new Point(444,580),new Point(0,580)});
  using(var line=new Pen(Color.FromArgb(46,229,187,112),1)){g.DrawLine(line,0,473,85,449);g.DrawLine(line,181,487,310,431);g.DrawLine(line,443,0,443,580);}
  using(var fleck=new SolidBrush(Color.FromArgb(115,229,187,112))){for(int i=0;i<20;i++){int x=25+(i*67)%390,y=280+(i*41)%200;g.FillEllipse(fleck,x,y,i%3==0?3:1,i%3==0?3:1);}}
 }
}
public sealed class AccountWindow : Form {
 readonly int port;
 readonly string clientRoot,labRoot,eventsPath;
 readonly TextBox account=new TextBox(),password=new TextBox(),confirmation=new TextBox(),nickname=new TextBox();
 readonly TextBox invitation=new TextBox();
 readonly Button serverSettings=new Button();
 readonly Label serverAddress=new Label(),invitationLabel=new Label();
 readonly ComboBox regions=new ComboBox();
 readonly Button register=new Button(),login=new Button(),enter=new Button(),logout=new Button();
 readonly Button loginMode=new Button(),registerMode=new Button(),refresh=new Button();
 readonly CheckBox reveal=new CheckBox();
 readonly Label confirmationLabel=new Label(),nicknameLabel=new Label(),identity=new Label(),phase=new Label();
 readonly TableLayoutPanel fields=new TableLayoutPanel();
 readonly ProgressBar progress=new ProgressBar();
 readonly ErrorProvider errors=new ErrorProvider();
 readonly Color accent=Color.FromArgb(229,187,112);
 readonly Panel credentialsPanel=new Panel(),regionPanel=new Panel();
 readonly Label cardTitle=new Label(),cardSubtitle=new Label();
 readonly Label status=new Label();
 Form authenticationError;
 string session;
 long uid;
 bool busy,registering,entered;
 string entryReceipt;
 NativeCloudOptions native;
 sealed class RegionChoice { public int Id; public string Name; public override string ToString(){return Name;} }

 public static void Run(int port,string clientRoot,string labRoot,string eventsPath) {
  Application.EnableVisualStyles();
  Application.SetCompatibleTextRenderingDefault(false);
  Application.Run(new AccountWindow(port,clientRoot,labRoot,eventsPath));
 }
 public static void RunNative(NativeCloudOptions options,string clientRoot,string labRoot,string eventsPath){
  options.Validate();Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  var window=new AccountWindow(options.auth_port,clientRoot,labRoot,eventsPath);window.ConfigureNative(options);
  Application.Run(window);
 }
 public void ConfigureNative(NativeCloudOptions options){
  if(busy||session!=null)throw new InvalidOperationException("logout_before_server_change");
  native=LauncherSettings.Load(Path.Combine(labRoot,"server-settings.json"),options);native.Validate();Text="功夫小子 · 登录";serverSettings.Visible=true;UpdateServerLabel();SetMode(false);
 }
 void UpdateServerLabel(){serverAddress.Text=native==null?"本地服务 · 不保存密码":"服务器  "+native.sdk_host+"  ·  TLS 加密登录";}
 void ChangeServer(){
  if(native==null||busy||session!=null)return;
  using(var dialog=new ServerSettingsWindow(native)){if(dialog.ShowDialog(this)!=DialogResult.OK)return;try{LauncherSettings.Save(Path.Combine(labRoot,"server-settings.json"),dialog.Result);native=dialog.Result;password.Clear();confirmation.Clear();UpdateServerLabel();status.Text="服务器设置已保存，请登录该服务器的账号。";}catch(IOException){ShowError("settings_save_failed");}catch(UnauthorizedAccessException){ShowError("settings_save_failed");}}
 }
 AccountWindow(int authPort,string root,string lab,string events) {
  port=authPort;clientRoot=Path.GetFullPath(root);labRoot=Path.GetFullPath(lab);eventsPath=String.IsNullOrWhiteSpace(events)?Path.Combine(labRoot,"events.jsonl"):Path.GetFullPath(events);
  Text="功夫小子 · 本地登录";ClientSize=new Size(900,580);StartPosition=FormStartPosition.CenterScreen;
  FormBorderStyle=FormBorderStyle.None;MaximizeBox=false;AutoScaleMode=AutoScaleMode.Dpi;
  Font=new Font("Microsoft YaHei UI",10);BackColor=Color.FromArgb(20,23,28);ForeColor=Color.FromArgb(232,232,230);DoubleBuffered=true;
  errors.ContainerControl=this;errors.BlinkStyle=ErrorBlinkStyle.NeverBlink;
  var artwork=new ThemePanel{Location=new Point(0,0),Size=new Size(444,580)};Controls.Add(artwork);
  var brand=new Label{Text="功夫小子",Location=new Point(36,26),Size=new Size(240,38),Font=new Font("Microsoft YaHei UI",22,FontStyle.Bold),ForeColor=accent,BackColor=Color.Transparent};artwork.Controls.Add(brand);
  artwork.Controls.Add(new Label{Text="再赴擂台",Location=new Point(36,154),Size=new Size(365,68),Font=new Font("KaiTi",42,FontStyle.Bold),ForeColor=Color.FromArgb(246,228,188),BackColor=Color.Transparent});
  artwork.Controls.Add(new Label{Text="一招一式，重逢当年。",Location=new Point(42,234),Size=new Size(340,32),Font=new Font("Microsoft YaHei UI",12),ForeColor=Color.FromArgb(172,167,151),BackColor=Color.Transparent});
  artwork.Controls.Add(new Label{Text="重逢江湖  /  KUNG FU KID",Location=new Point(40,521),Size=new Size(360,25),Font=new Font("Microsoft YaHei UI",9),ForeColor=Color.FromArgb(145,145,137),BackColor=Color.Transparent});
  StyleButton(serverSettings,"服务器设置",122);serverSettings.SetBounds(666,12,126,30);serverSettings.Visible=false;serverSettings.Click+=delegate{ChangeServer();};Controls.Add(serverSettings);
  var close=new Button();StyleButton(close,"×",40);close.TabStop=false;close.Font=new Font(Font.FontFamily,18);close.Location=new Point(850,8);close.Click+=delegate{Close();};Controls.Add(close);
  var minimize=new Button();StyleButton(minimize,"—",40);minimize.TabStop=false;minimize.Location=new Point(808,8);minimize.Click+=delegate{WindowState=FormWindowState.Minimized;};Controls.Add(minimize);
  Point dragStart=Point.Empty;MouseDown+=delegate(object sender,MouseEventArgs e){if(e.Button==MouseButtons.Left)dragStart=e.Location;};MouseMove+=delegate(object sender,MouseEventArgs e){if(e.Button==MouseButtons.Left)Location=new Point(Location.X+e.X-dragStart.X,Location.Y+e.Y-dragStart.Y);};
  // Keep the feedback strip outside this panel. A 460px panel covered its
  // sibling status label on real WinForms windows despite DrawToBitmap output.
  credentialsPanel.SetBounds(498,72,344,410);Controls.Add(credentialsPanel);
  StyleButton(loginMode,"账号登录",110);StyleButton(registerMode,"注册账号",110);registerMode.Left=124;credentialsPanel.Controls.Add(loginMode);credentialsPanel.Controls.Add(registerMode);
  cardTitle.SetBounds(0,58,340,36);cardTitle.Font=new Font(Font.FontFamily,20,FontStyle.Bold);credentialsPanel.Controls.Add(cardTitle);
  cardSubtitle.SetBounds(0,103,344,28);cardSubtitle.Font=new Font(Font.FontFamily,9);cardSubtitle.ForeColor=Color.FromArgb(146,150,155);credentialsPanel.Controls.Add(cardSubtitle);
  fields.SetBounds(0,144,344,154);fields.Margin=Padding.Empty;fields.ColumnCount=1;fields.RowCount=6;fields.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
  for(int i=0;i<5;i++)fields.RowStyles.Add(new RowStyle(SizeType.Absolute,62));fields.RowStyles.Add(new RowStyle(SizeType.Absolute,26));
  AddField(new Label{Text="账号"},account,0);account.MaxLength=20;
  AddField(new Label{Text="密码"},password,1);password.MaxLength=256;password.UseSystemPasswordChar=true;
  confirmationLabel.Text="确认密码";AddField(confirmationLabel,confirmation,2);confirmation.MaxLength=256;confirmation.UseSystemPasswordChar=true;
  nicknameLabel.Text="角色昵称（选填）";AddField(nicknameLabel,nickname,3);nickname.MaxLength=20;
  invitationLabel.Text="邀请码";AddField(invitationLabel,invitation,4);invitation.MaxLength=128;
  reveal.Text="显示密码";reveal.AutoSize=true;reveal.Font=new Font(Font.FontFamily,9);reveal.ForeColor=Color.FromArgb(155,158,162);fields.Controls.Add(reveal,0,5);credentialsPanel.Controls.Add(fields);
  StyleButton(login,"登  录",344,true);StyleButton(register,"创 建 账 号",344,true);credentialsPanel.Controls.Add(login);credentialsPanel.Controls.Add(register);
  regionPanel.SetBounds(498,130,344,346);Controls.Add(regionPanel);
  regionPanel.Controls.Add(new Label{Text="选择区服",Font=new Font(Font.FontFamily,22,FontStyle.Bold),Size=new Size(344,46)});
  identity.SetBounds(0,56,344,40);identity.ForeColor=Color.FromArgb(156,160,167);regionPanel.Controls.Add(identity);
  regions.SetBounds(0,120,344,34);regions.DropDownStyle=ComboBoxStyle.DropDownList;regions.FlatStyle=FlatStyle.Flat;regions.BackColor=Color.FromArgb(36,40,47);regions.ForeColor=ForeColor;regions.AccessibleName="游戏区服";regionPanel.Controls.Add(regions);
  StyleButton(refresh,"刷新列表",100);refresh.SetBounds(244,160,100,32);regionPanel.Controls.Add(refresh);StyleButton(enter,"进 入 游 戏",344,true);enter.Location=new Point(0,222);regionPanel.Controls.Add(enter);
  StyleButton(logout,"退出账号",104);logout.Location=new Point(116,282);regionPanel.Controls.Add(logout);
  progress.SetBounds(498,534,344,3);progress.Style=ProgressBarStyle.Blocks;Controls.Add(progress);
  phase.Visible=false;
  status.SetBounds(498,489,344,42);status.Font=new Font(Font.FontFamily,9);status.ForeColor=Color.FromArgb(157,160,166);status.AccessibleName="操作状态";status.TextAlign=ContentAlignment.MiddleLeft;status.Padding=new Padding(6,0,6,0);Controls.Add(status);status.BringToFront();
  serverAddress.SetBounds(488,548,366,22);serverAddress.TextAlign=ContentAlignment.MiddleCenter;serverAddress.Font=new Font(Font.FontFamily,8);serverAddress.ForeColor=Color.FromArgb(105,111,121);Controls.Add(serverAddress);UpdateServerLabel();
  register.Click+=delegate{Register();};login.Click+=delegate{Login();};enter.Click+=delegate{EnterGame();};logout.Click+=delegate{Logout();};
  loginMode.Click+=delegate{SetMode(false);};registerMode.Click+=delegate{SetMode(true);};refresh.Click+=delegate{RefreshRegions();};
  reveal.CheckedChanged+=delegate{password.UseSystemPasswordChar=confirmation.UseSystemPasswordChar=!reveal.Checked;};
  regions.SelectedIndexChanged+=delegate{SetBusy(busy);};Shown+=delegate{account.Focus();};SetMode(false);
 }
 void AddField(Label label,TextBox box,int row){var holder=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty};label.SetBounds(0,0,340,20);label.ForeColor=Color.FromArgb(161,165,172);label.Font=new Font(Font.FontFamily,9);holder.Controls.Add(label);var border=new Panel{Location=new Point(0,20),Size=new Size(344,28),BackColor=Color.FromArgb(35,39,47)};box.SetBounds(10,3,302,24);box.BorderStyle=BorderStyle.None;box.BackColor=border.BackColor;box.ForeColor=ForeColor;box.AccessibleName=label.Text;border.Controls.Add(box);holder.Controls.Add(border);fields.Controls.Add(holder,0,row);errors.SetIconAlignment(box,ErrorIconAlignment.MiddleRight);}
 void StyleButton(Button button,string text,int width,bool primary=false){button.Text=text;button.Size=new Size(width,primary?44:32);button.Cursor=Cursors.Hand;button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderSize=0;button.FlatAppearance.MouseOverBackColor=primary?Color.FromArgb(242,207,145):Color.FromArgb(35,39,47);button.BackColor=primary?accent:BackColor;button.ForeColor=primary?Color.FromArgb(35,28,18):Color.FromArgb(162,166,174);button.Font=new Font(Font.FontFamily,primary?11:10,primary?FontStyle.Bold:FontStyle.Regular);button.UseVisualStyleBackColor=false;}
 void SetMode(bool value){
  if(busy||session!=null)return;registering=value;
  for(int row=0;row<5;row++){bool visible=row<2||value&&(row<4||native!=null);fields.GetControlFromPosition(0,row).Visible=visible;fields.RowStyles[row].Height=visible?(value&&native!=null?50:62):0;}
  confirmation.Visible=confirmationLabel.Visible=nickname.Visible=nicknameLabel.Visible=value;invitation.Visible=invitationLabel.Visible=value&&native!=null;
  fields.Top=value?72:144;fields.Height=value?276:150;register.Top=login.Top=fields.Bottom+18;register.Visible=value;login.Visible=!value;cardTitle.Visible=cardSubtitle.Visible=!value;cardTitle.Text="欢迎回来";cardSubtitle.Text="登录账号，回到熟悉的擂台。";confirmation.Clear();reveal.Checked=false;errors.Clear();loginMode.ForeColor=value?Color.FromArgb(128,134,145):accent;registerMode.ForeColor=value?accent:Color.FromArgb(128,134,145);phase.Text=value?"创建账号":"1 / 3  登录账号";status.ForeColor=Color.FromArgb(157,160,166);status.Text=value?"账号 3–20 位字母或数字，密码 12–128 个字符。":"首次游玩？点击上方「注册账号」。";SetBusy(false);
 }
 void SetBusy(bool value){busy=value;bool editable=!value&&session==null;credentialsPanel.Visible=session==null;regionPanel.Visible=session!=null;serverSettings.Enabled=editable;account.Enabled=password.Enabled=confirmation.Enabled=nickname.Enabled=invitation.Enabled=reveal.Enabled=loginMode.Enabled=registerMode.Enabled=editable;register.Enabled=login.Enabled=editable;regions.Enabled=refresh.Enabled=!value&&session!=null&&!entered;enter.Enabled=!value&&session!=null&&!entered&&regions.SelectedItem!=null;logout.Enabled=!value&&session!=null;enter.Text=entered?"已进入游戏":"进 入 游 戏";progress.Visible=value;progress.Style=value?ProgressBarStyle.Marquee:ProgressBarStyle.Blocks;progress.MarqueeAnimationSpeed=value?30:0;AcceptButton=value?null:(session!=null?(IButtonControl)enter:(registering?(IButtonControl)register:login));if(value)reveal.Checked=false;}
 protected override void Dispose(bool disposing){if(disposing)errors.Dispose();base.Dispose(disposing);}
 protected override void OnFormClosing(FormClosingEventArgs e) {
  // Task.Run uses background threads: closing the last form used to abandon
  // initialization after injection, before status polling and SDK cleanup.
  if(busy&&e.CloseReason==CloseReason.UserClosing){e.Cancel=true;WindowState=FormWindowState.Minimized;status.Text="操作仍在后台进行，完成后可以关闭窗口。";return;}
  base.OnFormClosing(e);
 }
 void EntryStage(string phase,string error=null) {
  if(entryReceipt==null)return;
  var row=new {schema="kk-local-entry-progress-v1",phase=phase,error=error,observed_at=DateTime.UtcNow.ToString("o")};
  string temporary=entryReceipt+".tmp";
  File.WriteAllText(temporary,new JavaScriptSerializer().Serialize(row),new UTF8Encoding(false));
  if(File.Exists(entryReceipt))File.Replace(temporary,entryReceipt,null);else File.Move(temporary,entryReceipt);
 }
 void Ui(Action action){if(IsDisposed||!IsHandleCreated)return;try{BeginInvoke(action);}catch(InvalidOperationException){/* Window closed during completion. */}}
 void Progress(string text){Ui(delegate{status.Text=text;});}
 static byte[] ReadExact(Stream stream,int n){byte[] b=new byte[n];int at=0;while(at<n){int got=stream.Read(b,at,n-at);if(got==0)throw new IOException();at+=got;}return b;}
 object Call(string operation,Dictionary<string,object> arguments) {
  if(native!=null)return NativeCloudRpc.CallConfigured(native,operation,arguments);
  JavaScriptSerializer json=new JavaScriptSerializer();
  byte[] data=Encoding.UTF8.GetBytes(json.Serialize(new {schema="kk-local-auth-v1",operation=operation,arguments=arguments}));
  using(TcpClient client=new TcpClient()) {
   try {
    var connect=client.ConnectAsync("127.0.0.1",port);if(!connect.Wait(5000))throw new IOException();
    using(NetworkStream stream=client.GetStream()) {
     stream.ReadTimeout=20000;stream.WriteTimeout=5000;
     byte[] head={(byte)(data.Length>>24),(byte)(data.Length>>16),(byte)(data.Length>>8),(byte)data.Length};
     stream.Write(head,0,4);stream.Write(data,0,data.Length);stream.Flush();
     byte[] responseHead=ReadExact(stream,4);int n=(responseHead[0]<<24)|(responseHead[1]<<16)|(responseHead[2]<<8)|responseHead[3];
     if(n<2||n>65536)throw new IOException();
     var result=(Dictionary<string,object>)json.DeserializeObject(Encoding.UTF8.GetString(ReadExact(stream,n)));
     if(!(bool)result["ok"])throw new InvalidOperationException((string)result["error"]);
     return result["result"];
    }
   } finally {Array.Clear(data,0,data.Length);}
  }
 }
 static Dictionary<string,object> Args(params object[] pairs){var d=new Dictionary<string,object>();for(int i=0;i<pairs.Length;i+=2)d[(string)pairs[i]]=pairs[i+1];return d;}
 void Work(Action body,Action success) {
  if(busy)return;status.BackColor=BackColor;status.ForeColor=ForeColor;status.BorderStyle=BorderStyle.None;SetBusy(true);
  Task.Run(delegate {
   try {body();Ui(delegate{SetBusy(false);try{success();}catch(Exception){ShowError("invalid_service_response");}});}
   catch(Exception error){string code=error is System.Security.Authentication.AuthenticationException?"native_tls_validation_failed":error is InvalidOperationException&&Regex.IsMatch(error.Message,"^[a-z][a-z0-9_]{0,63}$")?error.Message:"local_connection_or_launch_failed";try{EntryStage("failed",code);}catch(IOException){}catch(UnauthorizedAccessException){}Ui(delegate{ShowError(code);});}
  });
 }
 void ShowError(string code){if(code=="invalid_session"||code=="native_session_expired"){session=null;uid=0;entered=false;regions.Items.Clear();identity.Text="登录已失效，请重新登录";phase.Text="1 / 3  登录账号";}SetBusy(false);status.BackColor=Color.FromArgb(61,31,35);status.BorderStyle=BorderStyle.FixedSingle;status.ForeColor=Color.FromArgb(255,173,159);status.Text=Explain(code);status.BringToFront();
  if(code=="invalid_credentials"){status.Text="登录失败：账号或密码不正确。";errors.SetError(password,"请检查账号、密码，以及当前服务器是否正确。");password.Focus();ShowAuthenticationError();}
  if(code=="invitation_required")status.Text="注册需要邀请码，请向服务器管理员获取。";
  if(code=="invalid_invitation"||code=="invitation_invalid")status.Text="邀请码无效、已使用或已过期。";
  if(code=="native_tls_validation_failed")status.Text="服务器证书校验失败，请核对 IP、证书名称和安装配置。";
  if(code=="settings_save_failed")status.Text="无法保存地址，请检查启动器数据目录的写入权限。";
  if(code=="local_connection_or_launch_failed")status.Text="连接或启动失败，请核对服务器地址、网络和启动记录。";
 }
 void ShowAuthenticationError(){
  // Owned, single-instance feedback: no credentials or raw server response.
  // Keep the persistent status banner after this dialog is dismissed.
  if(authenticationError!=null&&!authenticationError.IsDisposed){authenticationError.Activate();return;}
  var dialog=new Form{Text="登录失败",ClientSize=new Size(368,142),FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false,ShowInTaskbar=false,StartPosition=FormStartPosition.CenterParent,Font=Font,BackColor=BackColor,ForeColor=ForeColor};
  dialog.Controls.Add(new Label{Text="账号或密码不正确。\n请检查输入及所选服务器后重试。",Location=new Point(20,20),Size=new Size(328,60)});
  var ok=new Button{Text="确定",Location=new Point(250,94),Size=new Size(96,32)};dialog.Controls.Add(ok);dialog.AcceptButton=ok;dialog.CancelButton=ok;
  ok.Click+=delegate{dialog.Close();};dialog.FormClosed+=delegate{authenticationError=null;if(!IsDisposed)password.Focus();};
  authenticationError=dialog;dialog.Show(this);dialog.Activate();
 }
 static string ValidatePassword(string value){
  int count=0;for(int i=0;i<value.Length;i++,count++){if(Char.IsHighSurrogate(value[i])){if(i+1>=value.Length||!Char.IsLowSurrogate(value[++i]))return "invalid_password_encoding";}else if(Char.IsLowSurrogate(value[i]))return "invalid_password_encoding";}
  return count<12||count>128?"password_length_12_to_128_required":null;
 }
 bool ValidateFields(bool registration){
  errors.Clear();status.ForeColor=ForeColor;
  if(!Regex.IsMatch(account.Text,"\\A[A-Za-z0-9]{3,20}\\z")){errors.SetError(account,Explain("invalid_account_format"));ShowError("invalid_account_format");account.Focus();return false;}
  string error=ValidatePassword(password.Text);if(error!=null){errors.SetError(password,Explain(error));ShowError(error);password.Focus();return false;}
  if(registration&&password.Text!=confirmation.Text){errors.SetError(confirmation,"两次密码不一致。");confirmation.Focus();status.Text="两次密码不一致。";return false;}
  if(registration&&nickname.Text.Length>0){try{int size=Encoding.GetEncoding(936,EncoderFallback.ExceptionFallback,DecoderFallback.ExceptionFallback).GetByteCount(nickname.Text);if(size>20||nickname.Text.IndexOf('\0')>=0)throw new EncoderFallbackException();}catch(EncoderFallbackException){errors.SetError(nickname,"最多10个汉字或20个英文字符，不能包含表情等GBK不支持的字符。");nickname.Focus();status.Text="请检查角色昵称。";return false;}}
  return true;
 }
 void Register() {
  if(busy||session!=null||!ValidateFields(true))return;
  string name=account.Text,secret=password.Text,display=nickname.Text,invite=invitation.Text.Trim();password.Clear();confirmation.Clear();invitation.Clear();status.Text="正在注册…";
  Work(delegate{var args=Args("account",name,"password",secret);if(display.Length>0)args["nickname"]=display;if(native!=null)args["invite_code"]=invite;try{Call("register",args);}finally{args["password"]=null;args["invite_code"]=null;secret=null;invite=null;}},delegate{SetMode(false);status.Text="注册成功，请输入密码登录。";password.Focus();});
 }
 void Login() {
  if(busy||session!=null||!ValidateFields(false))return;
  string name=account.Text,secret=password.Text;Dictionary<string,object> result=null;password.Clear();confirmation.Clear();status.Text="正在校验密码…";
  Work(delegate{try{result=(Dictionary<string,object>)Call("login",Args("account",name,"password",secret));}finally{secret=null;}},delegate{session=(string)result["session"];uid=Convert.ToInt64(result["uid"]);entered=false;identity.Text="已登录："+name+"  ·  请选择区服";phase.Text="2 / 3  选择区服";RefreshRegions();});
 }
 void RefreshRegions(){
  if(busy||session==null||entered)return;string token=session;var list=new List<RegionChoice>();var previous=regions.SelectedItem as RegionChoice;int selected=previous==null?-1:previous.Id;status.ForeColor=ForeColor;status.Text="正在获取区服…";
  Work(delegate{foreach(object item in (object[])Call("regions",Args("session",token))){var row=(Dictionary<string,object>)item;list.Add(new RegionChoice{Id=Convert.ToInt32(row["id"]),Name=(string)row["name"]});}},delegate{regions.Items.Clear();foreach(var item in list){regions.Items.Add(item);if(item.Id==selected)regions.SelectedItem=item;}if(regions.SelectedIndex<0&&regions.Items.Count>0)regions.SelectedIndex=0;SetBusy(false);status.Text=list.Count==0?"暂无可用区服，可以稍后点击刷新。":"密码验证通过，选择区服后点击进入游戏。";});
 }
 static string Quote(string value){if(value.IndexOfAny(new[]{'"','\r','\n','\0'})>=0)throw new InvalidOperationException("invalid_local_path");return "\""+value.TrimEnd('\\')+"\"";}
 void Helper(string script,string arguments,int timeout) {
  string path=Path.Combine(labRoot,script);if(!File.Exists(path))throw new InvalidOperationException("missing_local_tools");
  ProcessStartInfo info=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe"),"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "+Quote(path)+" "+arguments);
  info.UseShellExecute=false;info.CreateNoWindow=true;info.WindowStyle=ProcessWindowStyle.Hidden;
  using(Process p=Process.Start(info)){if(!p.WaitForExit(timeout)){p.Kill();throw new InvalidOperationException("local_launch_timeout");}if(p.ExitCode!=0)throw new InvalidOperationException("local_entry_failed");}
 }
 void EnterGame() {
  if(busy||session==null||entered)return;
  RegionChoice selected=regions.SelectedItem as RegionChoice;if(selected==null)return;
  if(native!=null){EnterNative(selected);return;}
  string token=session;long user=uid;int region=selected.Id;
  entryReceipt=null;phase.Text="3 / 3  启动游戏";status.ForeColor=ForeColor;
  Work(delegate{
   string image=Path.Combine(clientRoot,"gfxz-lab.exe");
   foreach(Process process in Process.GetProcessesByName("gfxz-lab")){using(process){try{if(String.Equals(process.MainModule.FileName,image,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("client_already_running");}catch(System.ComponentModel.Win32Exception){throw new InvalidOperationException("client_identity_unavailable");}}}
   var selectedReply=(Dictionary<string,object>)Call("select_region",Args("session",token,"region_id",region));
   string ticket=(string)selectedReply["ticket"];
   string runRoot=Path.Combine(labRoot,"password-login-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,8));Directory.CreateDirectory(runRoot);
   entryReceipt=Path.Combine(runRoot,"entry-progress.json");EntryStage("starting_client");
   Progress("正在启动原客户端…");
   Helper("Start-Kk1LobbyClientGuest.ps1","-ClientRoot "+Quote(clientRoot)+" -EvidenceRoot "+Quote(runRoot),45000);
   var launch=(Dictionary<string,object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(runRoot,"launch.json")));
   int pid=Convert.ToInt32(launch["pid"]);
   Call("bind_client",Args("ticket",ticket,"uid",user,"region_id",region,"pid",pid));ticket=null;
   EntryStage("client_bound");
   Progress("账号已绑定，等待原客户端初始化…");
   Helper("Complete-Kk1AuthenticatedEntryGuest.ps1","-ExpectedPid "+pid+" -ClientRoot "+Quote(clientRoot)+" -RunRoot "+Quote(runRoot),270000);
   EntryStage("waiting_lobby");
   DateTime deadline=DateTime.UtcNow.AddSeconds(180);bool ready=false;
   while(DateTime.UtcNow<deadline){var state=(Dictionary<string,object>)Call("status",Args("session",token,"pid",pid));Progress("原客户端阶段："+(string)state["stage"]);if((bool)state["lobby_ready"]){ready=true;break;}Thread.Sleep(1000);}
   if(!ready)throw new InvalidOperationException("local_launch_timeout");
   EntryStage("hiding_sdk_overlay");
   Helper("Hide-KKClientLoginOverlay.ps1","-ExpectedPid "+pid+" -SdoClientRoot "+Quote(clientRoot)+" -GameTracePath "+Quote(eventsPath)+" -ReceiptPath "+Quote(Path.Combine(runRoot,"login-overlay.json")),20000);
   var hidden=(Dictionary<string,object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(runRoot,"login-overlay.json")));
   if((string)hidden["status"]!="hidden"&&(string)hidden["status"]!="already_absent")throw new InvalidOperationException("overlay_check_failed");
   EntryStage("completed");entryReceipt=null;
  },delegate{entered=true;SetBusy(false);phase.Text="进入完成";status.Text="已进入游戏，可以关闭此窗口；关闭窗口不会退出账号。";});
 }
 void EnterNative(RegionChoice selected){
  string token=session;long user=uid;int region=selected.Id;string name=account.Text;
  Work(delegate{
   string runRoot=Path.Combine(labRoot,"native-login-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,8));Directory.CreateDirectory(runRoot);
   entryReceipt=Path.Combine(runRoot,"entry-progress.json");EntryStage("starting_native_client");Progress("启动原客户端，等待SDK模块…");
   Dictionary<string,object> grant=null;int pid=0;string exe=Path.Combine(clientRoot,"gfxz-lab.exe");
   using(var process=NativeCloudHandoff.StartClient(clientRoot,runRoot,native)){
    pid=process.Id;
    try{
     NativeCloudHandoff.Inject(pid,exe,native.injector_path,native.adapter_dll);
     NativeCloudHandoff.WaitInstalled(pid,exe,native.adapter_dll,29,150);
     EntryStage("native_sdk_hooks_ready");
     Progress("正在加载客户端资源…");
     LauncherSettings.Wait(process,150,"original_login_not_ready",delegate{return NativeCloudHandoff.RouterSelector(pid,exe)==4&&NativeCloudHandoff.RoleReady(pid,exe);});
     EntryStage("original_login_ready");
     var selection=(Dictionary<string,object>)Call("select_region",Args("session",token,"region_id",region));
     grant=(Dictionary<string,object>)Call("authorize_game",Args("ticket",selection["ticket"],"region_id",region));selection["ticket"]=null;
     if(Convert.ToInt64(grant["uid"])!=user)throw new InvalidOperationException("native_identity_mismatch");
     byte[] packed=NativeCloudHandoff.Pack(pid,name,grant,native.sdk_host,native.sdk_port);
     try{if(NativeCloudHandoff.Invoke(pid,exe,native.adapter_dll,"KkNativeCloudSetTicket",packed)!=0)throw new InvalidOperationException("native_configuration_rejected");}finally{Array.Clear(packed,0,packed.Length);}
     EntryStage("native_tickets_installed");Progress("账号已授权，执行已有的原客户端初始化…");
     if(Convert.ToInt32(grant["game_port"])!=native.game_port||Convert.ToInt32(grant["udp_port"])!=native.udp_port)throw new InvalidOperationException("native_endpoint_mismatch");
     if(String.IsNullOrWhiteSpace(native.initializer_log))throw new InvalidOperationException("missing_initializer_log_setting");
     long logOffset=File.Exists(native.initializer_log)?new FileInfo(native.initializer_log).Length:0;
     NativeCloudHandoff.Inject(pid,exe,native.injector_path,native.initializer_dll);
     NativeCloudHandoff.WaitInstalled(pid,exe,native.adapter_dll,31,90);
     if(native.initializer_manual_start){LauncherSettings.Wait(process,5,"manual_initializer_not_ready",delegate{uint code=NativeCloudHandoff.Invoke(pid,exe,native.initializer_dll,"KkOfficialFlowBegin",null);if(code!=0&&code!=21)throw new InvalidOperationException("manual_initializer_failed");return code==0;});}
     LauncherSettings.Wait(process,120,"native_role_table_timeout",delegate{return NativeCloudHandoff.RoleReady(pid,exe)&&LauncherSettings.FreshInitializerReady(native.initializer_log,logOffset,pid);});
     EntryStage("native_ui_initialized");
     LauncherSettings.Wait(process,30,"native_profile_selector_not_ready",delegate{return NativeCloudHandoff.RouterSelector(pid,exe)==5;});
     Call("client_ready",Args("session",token,"game_credential",grant["game_credential"]));
     NativeCloudHandoff.HideSdk(pid,clientRoot);
     EntryStage("native_role_ready");Progress("等待云端确认大厅连接…");
     Stopwatch stable=null;
     LauncherSettings.Wait(process,120,"native_lobby_timeout",delegate{var result=(Dictionary<string,object>)Call("entry_status",Args("session",token,"uid",user));if((bool)result["lobby_ready"]&&NativeCloudHandoff.RouterSelector(pid,exe)==6){if(stable==null)stable=Stopwatch.StartNew();return stable.Elapsed.TotalSeconds>=5;}stable=null;return false;});
     NativeCloudHandoff.HideSdk(pid,clientRoot);EntryStage("completed");entryReceipt=null;
    }catch{
     if(grant!=null){try{Call("cancel_game",Args("session",token,"game_credential",grant["game_credential"]));}catch(Exception){}}
     try{NativeCloudHandoff.Invoke(pid,exe,native.adapter_dll,"KkNativeCloudRemove",null);}catch(Exception){}
     throw;
    }finally{if(grant!=null)grant.Clear();}
   }
  },delegate{entered=true;SetBusy(false);phase.Text="进入完成";status.Text="服务端已确认大厅连接；可以关闭登录窗口。";});
 }
 void Logout(){if(busy||session==null)return;if(MessageBox.Show(this,"退出账号会断开对应的游戏连接，但不会自动关闭游戏。继续？","退出账号",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;string token=session;Work(delegate{Call("logout",Args("session",token));},delegate{session=null;uid=0;entered=false;regions.Items.Clear();identity.Text="尚未登录 · 登录后选择区服";SetMode(false);status.Text="已退出账号。再次进入前，请先关闭原游戏进程。";});}
 static string Explain(string code){switch(code){case "invalid_credentials":return "账号或密码不正确，请重新输入。";case "rate_limited":return "尝试次数过多，请稍后再试；不要连续点击登录。";case "account_unavailable":return "账号已存在或保留，不能重复注册。";case "invalid_account_format":return "账号需为3–20位字母或数字。";case "password_length_12_to_128_required":return "密码长度需为12–128个字符。";case "invalid_password_encoding":case "password_too_long":return "密码包含无效字符或超出长度限制。";case "invalid_nickname":return "昵称需为1–20个GBK字节（最多10个汉字），不能包含表情。";case "client_already_running":return "游戏已经在运行，请先关闭原游戏，避免重复启动。";case "client_identity_unavailable":return "无法确认已有游戏进程，请检查权限，不会重复启动。";case "local_launch_timeout":case "local_entry_failed":return "游戏交接未完成。请保留当前游戏，检查启动记录；不要反复点击进入。";case "invalid_session":case "native_session_expired":return "登录已失效，请重新登录；重新进入前先关闭旧游戏。";case "native_process_rejected":return "启动的客户端身份校验失败。";case "invalid_ticket":return "进入凭据已失效，请确认没有遗留游戏进程后重试。";case "missing_local_tools":return "启动工具未部署完整，请修复安装。";case "overlay_check_failed":return "大厅已建立，但旧登录层隐藏失败，请检查交接记录。";case "local_connection_or_launch_failed":return "无法连接本地服务或启动工具，请检查服务后重试。";default:return "操作未完成，请检查本地服务和启动工具（"+code+"）。";}}
}
}
