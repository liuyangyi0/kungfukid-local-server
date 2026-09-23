[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path -LiteralPath $compiler)){throw '.NET Framework C# compiler is required on the build machine'}
New-Item -ItemType Directory -Path $output -Force|Out-Null
$exe=Join-Path $output '功夫小子启动器.exe'
$iconPath=Join-Path $output 'launcher.ico'
# Original geometric icon. No game artwork is extracted or redistributed.
Add-Type -AssemblyName System.Drawing
if(-not('KkLauncherIconNative'-as[type])){Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class KkLauncherIconNative{[DllImport("user32.dll")]public static extern bool DestroyIcon(IntPtr icon);}'}
$bitmap=New-Object Drawing.Bitmap(32,32)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
$font=New-Object Drawing.Font('Segoe UI',20,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$brush=New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(229,187,112))
try{
 $graphics.Clear([Drawing.Color]::FromArgb(20,23,28));$graphics.TextRenderingHint=[Drawing.Text.TextRenderingHint]::AntiAliasGridFit
 $graphics.DrawString('K',$font,$brush,7,2)
 $handle=$bitmap.GetHicon();$icon=[Drawing.Icon]::FromHandle($handle);$stream=[IO.File]::Create($iconPath)
 try{$icon.Save($stream)}finally{$stream.Dispose();$icon.Dispose();[void][KkLauncherIconNative]::DestroyIcon($handle)}
}finally{$brush.Dispose();$font.Dispose();$graphics.Dispose();$bitmap.Dispose()}
$arguments=@('/nologo','/target:winexe','/platform:x64','/optimize+','/debug-','/codepage:65001',('/out:'+$exe),('/win32manifest:'+(Join-Path $PSScriptRoot 'launcher.manifest')),('/win32icon:'+$iconPath))
foreach($assembly in @('System.dll','System.Core.dll','System.Windows.Forms.dll','System.Drawing.dll','System.Web.Extensions.dll','System.Xml.dll')){$arguments+=('/reference:'+(Join-Path (Split-Path $compiler -Parent) $assembly))}
$arguments+=@(Join-Path $PSScriptRoot 'LauncherProgram.cs'),@(Join-Path $PSScriptRoot 'LocalAccountWindow.cs'),@(Join-Path $PSScriptRoot 'NativeCloudHandoff.cs')
& $compiler @arguments
if($LASTEXITCODE-ne0-or-not(Test-Path -LiteralPath $exe)){throw 'Launcher compilation failed'}
[IO.File]::WriteAllText(($exe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup useLegacyV2RuntimeActivationPolicy="true"><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',(New-Object Text.UTF8Encoding($false)))
Write-Output $exe
