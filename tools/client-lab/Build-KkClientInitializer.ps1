[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$out=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh output directory'}
if($out.IndexOfAny([char[]]'"&|<>%') -ge 0 -or $out.Contains("`n") -or $out.Contains("`r")){throw 'Unsupported build path characters'}
$vc='C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat'
if(-not(Test-Path -LiteralPath $vc)){throw 'VS2022 Community with MSVC 14.36 x86 is required'}
$source=Join-Path $PSScriptRoot 'kk1_official_flow_probe.c'
$definition=Join-Path $PSScriptRoot 'official-flow\initializer.def'
$model=Join-Path $PSScriptRoot 'official-flow\initializer_model.c'
# The release tree has a different layout for the existing injector source.
$injector=Join-Path $PSScriptRoot 'net_hook\kk_inject.c'
if(-not(Test-Path -LiteralPath $injector)){$injector=Join-Path $PSScriptRoot '..\..\client-adapter\src\kk_inject.c'}
foreach($path in @($source,$definition,$model,$injector)){
 if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing source: $path"}
 if($path.IndexOfAny([char[]]'"&|<>%') -ge 0){throw 'Unsupported source path characters'}
}
New-Item -ItemType Directory -Path $out | Out-Null
$flags=' /nologo /TC /W4 /WX /utf-8 /MT /D_CRT_SECURE_NO_WARNINGS'
$command='call "'+$vc+'" x86 -vcvars_ver=14.36 >nul'
$command+=' && cl'+$flags+' /LD /DKK_OFFICIAL_FLOW_MANUAL_START /DKK_OFFICIAL_FLOW_PORTABLE "'+$source+'" /Fo"'+$out+'\initializer.obj" /link user32.lib /DEF:"'+$definition+'" /OUT:"'+$out+'\kk1-official-flow.dll"'
$command+=' && cl'+$flags+' "'+$model+'" /Fo"'+$out+'\model.obj" /Fe"'+$out+'\initializer-model.exe"'
$command+=' && "'+$out+'\initializer-model.exe" "'+$out+'\kk1-official-flow.dll" --readonly-log'
$command+=' && "'+$out+'\initializer-model.exe" "'+$out+'\kk1-official-flow.dll"'
$command+=' && cl'+$flags+' "'+$injector+'" /Fo"'+$out+'\injector.obj" /Fe"'+$out+'\kk_inject.exe"'
Push-Location -LiteralPath $out
try{& $env:ComSpec /d /c $command;if($LASTEXITCODE){throw 'Initializer build or owned-model test failed'}}finally{Pop-Location}
[ordered]@{status='built-and-model-tested';architecture='i686';manual_start=$true;log_location='DLL directory';dll=(Join-Path $out 'kk1-official-flow.dll');injector=(Join-Path $out 'kk_inject.exe');original_client_tested=$false}|ConvertTo-Json
