# 客户端版本与适配边界

推荐使用[独立 C# 登录器](../tools/client-lab/native-cloud-ticket/README.md)连接自建账号服务。原 SDO 窗口的兼容代码仍可供接口适配参考，但不是新用户的部署主入口。

两条路径不能混为一谈：独立登录器替代旧窗口的账号输入与认证过程，游戏侧依然需要匹配的主程序、资源、SDLogin/SDP2P 与初始化组件。换登录器不能自动解决所有版本差异。

<a id="sdk-build-compatibility"></a>

### 客户端构建与 SDK 兼容性（issue #1）

实验本地副本的 `Version.dat` 中 `[Common] Version=1.13.0.594`。这是游戏构建标识，**不是登录 SDK 的兼容版本号**；相同游戏版本可以配有不同的 SDO 登录组件。以下是2026-09-23只读核对的本地参考组合，不是对所有同版本文件的支持承诺：

| 相对路径 | 文件版本 | 字节数 | SHA-256 |
|---|---|---:|---|
| `sdo/sdologin/SdoBaseClient.dll` | `2.2.2.0` | 264224 | `1C78ECFAD578A4508B0170C2B624F85D584C7EC06C191AA38F61093133E1EB2B` |
| `sdo/sdologin/sdologin.exe` | 未取得版本资源 | 1242656 | `F344173CF281A14EA38DA99669205C304635A4F73D6592BA811B89CB5073D14C` |
| `sdo/sdologin/duilib.dll` | 未取得版本资源 | 1404448 | `A4FD0B57CCA0A517E53601CAD3DB6BB99C6F31354835E341B116F0AA0BC6A908` |

哈希只用于识别组件，不用于解锁，也不意味着可以重新分发这些文件。该副本来自项目持有者提供的本地安装/恢复资料；目前没有经维护者核实、可推荐的公开完整安装包获取地址。本仓库不分发原客户端或 SDK，不建议从不明来源拼装 DLL 或将不同版本组件混用。

参考 `SdoBaseClient.dll` 的导出序号16 RVA是 `0xc1b0`。issue #1 报告的 `2.3.3.0 / 0xc8c0` **尚未适配或验证**，`expected_export16_rva_match=false` 是预期的安全拒绝，不代表四个服务端端口异常，也不能据此认定原文件损坏。

不要只把检查工具或 C++ 中的 `0xc1b0` 改为 `0xc8c0`：请求适配器还依赖端点字符串对象、赋值函数、调用约定、公钥与密码 store；输入模块另外依赖 `duilib.dll` 控件/虚表布局。一个导出匹配也只是必要检查之一，工具继续返回 `version_compatibility_proven=false`。

欢迎针对2.3.3.0的适配贡献，但目前没有已完成实现或承诺交付日期。建议先在 issue 提供以下**非敏感元数据**：上述三文件的版本/大小/SHA-256、导出序号16 RVA、`Version.dat` 的版本值，以及检查工具的文件条目（删去本机绝对路径）。不要上传原 DLL/EXE、完整客户端、内存转储、账号口令、私钥或认证报文。

适配贡献应单独定义新版本配置，保留旧版本拒绝/兼容分支，确认各 ABI 与对象布局，附上合成合同测试和本地首次登录、错误密码、重试、窗口重建、退出/重登验证结果；匹配失败必须在修改对象/IAT前停止。代码与测试可以通过 PR 提交，不能用修改检查常量代替这些验证。独立账号登录/云端接入是另一条入口，也有自己的模块合同，不是已验证支持2.3.3.0的替代声明。


## 使用前如何检查

```powershell
python client-adapter/tools/inspect_client.py --client-root C:\MyGame
```

工具只读取指定目录中的文件并报告架构、导出及摘要，不修改文件、不读取账号库。它的 `candidate_only` 只表示部分检查匹配，不是完整版本验收。

只应使用同一已核对版本组合。遇到不匹配时先记录差异，不禁用系统防护、不加载旧反外挂驱动，也不替换来源不明的 DLL。服务端始终独立验证真实账号与授权，不因客户端检查通过而提升权限。

## 当前模块职责

| 源码 | 职责 |
|---|---|
| [LauncherProgram.cs](../tools/client-lab/LauncherProgram.cs) | 独立 GUI EXE 入口 |
| [LocalAccountWindow.cs](../tools/client-lab/LocalAccountWindow.cs) | 注册、登录、区服、服务器设置与错误提示 |
| [NativeCloudHandoff.cs](../tools/client-lab/NativeCloudHandoff.cs) | TLS 认证、内存凭据交接、版本与就绪检查 |
| [native-cloud-ticket](../tools/client-lab/native-cloud-ticket) | 原套接字票据/加密适配、IAT 覆盖和撤销生命周期 |
| [kk_sdo_request_adapter.cpp](../client-adapter/src/kk_sdo_request_adapter.cpp) | 原 SDO 窗口分支的请求重定向与公钥适配 |
| [kk_sdo_input_provider.cpp](../client-adapter/src/kk_sdo_input_provider.cpp) | 原窗口分支的密码输入兼容 |
| [kk_roleprop_observer.cpp](../client-adapter/src/kk_roleprop_observer.cpp) | 原客户端角色配置表就绪观察 |
| [inspect_client.py](../client-adapter/tools/inspect_client.py) | 只读组件信息检查 |

原窗口兼容源码含特定构建的布局和路径假设，不能直接当通用安装器。独立登录器的安装配置也必须匹配初始化器实际输出路径与导出接口。

适配会修改当前进程的导入槽或已确认的接口状态；“不修改磁盘上的 SDK”不等于“不写运行内存”。失败时应明确拒绝，不能退回未认证或明文链路。

## 移植到另一版本

每个版本至少核对以下契约：

1. **模块**：实际架构、导入/导出、文件身份和加载顺序。
2. **对象**：字段与虚表布局、分配/销毁时机及所有权。
3. **调用**：ABI、参数、隐藏返回值、所属线程和副作用。
4. **网络**：真实调用的同步/异步接口、缓存通知、关闭与重连行为。
5. **启动**：资源、角色表、授权、游戏连接和大厅就绪之间的顺序。

给新版本增加独立配置，保留旧版本兼容和拒绝分支。先跑合成测试，再记录目标版本的实际结果。不要在没有证据时把“能启动窗口”写成“能进大厅”，或把“进大厅”写成“战斗全部可用”。

贡献所需信息与 PR 清单见 [CONTRIBUTING](../CONTRIBUTING.md)。本仓库不接收原客户端、SDK 二进制、原始资源、转储或认证正文；提交自有源码、合成测试和脱敏结论即可。
