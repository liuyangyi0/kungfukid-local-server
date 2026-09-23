# 部署与客户端接入指南

当前推荐链路是：**Python public 服务 → C# 独立登录器 → 已适配的原客户端 → 大厅/房间与 UDP 中继**。

本指南只涉及自建服务，不需要旧运营服务。源码包不是完整游戏发行包：原游戏、匹配的初始化器与适配组件须自行合法准备。先看[支持版本](CLIENT_COMPATIBILITY.md#sdk-build-compatibility)。

## 1. 准备服务环境

- Python 3.12 与仓库 `requirements.txt` 中的依赖。
- 可持久化的独立数据和日志目录、足够磁盘空间。
- 有效 TLS 证书、私钥及其对应服务名称。
- 服务采用 SQLite 单进程写入，不能直接增加多个 worker 共用同一个库。

从源码根目录运行：

```shell
python -m pip install -r requirements.txt
python -m unittest discover -s server/tests -p "test_*.py" -v
```

不要只复制 `server/`：部分配置处理依赖同仓库 `tools/resource-recovery/` 工具。它们是源码模块，不是另一个 pip 包。

## 2. 创建配置和数据库

复制 [public.example.json](../server/configs/public.example.json) 和 [public-policy.example.json](../server/configs/public-policy.example.json)，保存在由管理员维护的配置目录。

必须修改数据库/日志路径、证书/私钥路径、策略文件路径、监听地址、对外地址和各服务端口。相对路径以配置加载规则为准，部署推荐使用绝对路径；示例默认回环，不会自动开放服务。

以下使用 Linux 示例路径，先创建目录并为专用服务用户配置最小权限：

```shell
python -m server.kk_local.public_admin --database /var/lib/kk-server/accounts.sqlite3 init
python -m server.kk_local.public_admin --database /var/lib/kk-server/accounts.sqlite3 invite --count 5
```

- `init` 用于新库。已有库先停服并使用迁移工具的一致性备份，不覆盖或重新初始化。
- 邀请码是敏感的一次性注册凭据，只私下交给测试用户，不贴到 issue 或提交源码。
- 管理工具与运行服务共享数据库租约；维护前停止服务，不能靠另开进程绕过。
- 用户已有账号可直接登录，不需要重新兑换邀请码。

备份、迁移、恢复与最小权限目录要求见[运维文档](../server/deploy/README.md)。SQLite 备份使用 backup API，不只复制可能遗漏 WAL 的主文件。

## 3. 检查并启动

```shell
python -m server.kk_local --settings /etc/kk-server/public.json --check-config
python -m server.kk_local --settings /etc/kk-server/public.json
```

只有配置检查通过再启动。持续运行可使用 [systemd 模板](../server/deploy/kk-public.service)，按目标系统核对路径和沙箱选项，不以 root 运行。

按实际配置开放：

| 通道 | 示例端口 | 用途 |
|---|---:|---|
| 认证 TLS | 17999/TCP | 注册、登录、选区和授权 |
| 身份初始化 | 18000/TCP | 已授权客户端的初始身份握手 |
| 游戏 | 18001/TCP | 大厅、房间与业务消息 |
| 对战中继 | 18001/UDP | 同房战斗数据转发 |
| 健康检查 | 18090/TCP | 仅回环，不向公网开放 |

上述是示例值；连接目标和服务公布的地址必须一致。不要把数据库、备份、私钥放入网站根目录，也不要修改已有网站配置来“借用”认证端口。

## 4. 准备兼容客户端

当前参考为 `Version.dat=1.13.0.594` 的已适配 i686 组合。原窗口 SDK 兼容分支只确认了 `SdoBaseClient.dll 2.2.2.0` 组合；`2.3.3.0` 尚未适配。独立登录器也有自己的模块合同，不能认为换窗口就能支持任意 SDK。

只读检查：

```powershell
python client-adapter/tools/inspect_client.py --client-root C:\MyGame
```

检查工具报告架构、摘要和导出等元数据，不证明已完成适配。出现身份或布局不匹配时停止，不改常量让检查强行通过。不要从不明来源拼装 DLL。

## 5. 构建和安装登录器

需要 Windows x64 / .NET Framework 4.8。按[登录器安装文档](../tools/client-lab/native-cloud-ticket/README.md)编译 EXE、准备工具与 `launcher.json`，再运行安装器。完整入口如下：

```powershell
powershell -NoProfile -File tools/client-lab/Build-KkLauncher.ps1 -OutputDirectory build/launcher
powershell -NoProfile -File tools/client-lab/Install-KkLauncher.ps1 -ClientRoot C:\MyGame -NativeCloudSettings C:\MyConfig\launcher.json -LauncherExecutable build/launcher/功夫小子启动器.exe -DesktopShortcut
```

安装器要求已准备的 `gfxz-lab.exe`、配套适配 DLL、注入器及初始化器；它不是游戏补丁制作器，不负责自动把任意客户端改成兼容版。

使用者双击 EXE，在“服务器设置”中填连接 IP、端口和 TLS 证书名称。证书名称不是文件路径；公开证书路径在安装配置中设置。不能关闭证书验证，也不能把服务器私钥分发给玩家。

## 6. 注册、登录与检查

1. 使用服务器发放的邀请码注册，或直接登录已有账号。
2. 检查错误密码是否有明确提示、是否仍停在登录页。
3. 登录成功后选择区服、进入游戏；确认角色与大厅实际显示。
4. 再检查房间、换队、准备、开局及结束，不把认证成功当作整条链路已通过。
5. 换电脑时复制完整已适配客户端与登录器安装目录；账号保存在服务端，不在启动器本地。

第三方客户端可直接按 [SERVICE](SERVICE.md) 接入，不必须调用本项目 DLL。

## 常见问题

| 现象 | 先检查什么 |
|---|---|
| 账号密码不正确 | 是否连接了正确服务器；不同服务器的账号库不自动共享 |
| 证书错误 | IP 与证书名称是否填混、有效期、系统信任链或固定公开证书是否匹配 |
| 已登录但进不了大厅 | 身份/游戏端口、公布端点、适配模块身份、角色初始化实际就绪情况 |
| SDK 布局检查不匹配 | 对照兼容矩阵；不能仅修改预期 RVA |
| EXE 单独复制后无法启动游戏 | 配套 `launcher/` 目录、EXE.config、自有工具与初始化器是否完整 |
| 能建连接但对战异常 | UDP 端口、房间/参战身份、限流及明确拒绝计数；不要关闭所有权限校验 |

对外分享诊断只保留阶段、错误类型和必要消息元数据，不上传账号库、完整认证日志或密钥。

## 上线前边界

公网非排位模式禁止由客户端战斗结果产生永久收益。100 在线是容量目标，不是部署保证；已有压力报告的收发差额仍待闭合。阅读[验证报告](../server/deploy/VALIDATION.md)和[安全说明](../SECURITY.md)，再按目标主机验证带宽、证书、权限与恢复流程。
