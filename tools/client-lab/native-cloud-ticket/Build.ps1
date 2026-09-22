[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$out=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh build directory; do not overwrite evidence'}
if($out.IndexOfAny([char[]]'"&|<>') -ge 0){throw 'Unsupported build path characters'}
$vc='C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat'
if(-not(Test-Path -LiteralPath $vc)){throw 'Pinned VS2022 MSVC toolchain required'}
New-Item -ItemType Directory -Path $out | Out-Null
$source=Join-Path $PSScriptRoot 'adapter.cpp'
$definition=Join-Path $PSScriptRoot 'adapter.def'
$command='call "'+$vc+'" x86 -vcvars_ver=14.36 >nul && cl /nologo /W4 /WX /utf-8 /std:c++17 /EHsc /MT /LD "'+$source+'" /Fo"'+$out+'\adapter.obj" /link /DEF:"'+$definition+'" /OUT:"'+$out+'\kk_native_cloud_ticket.dll" && cl /nologo /W4 /WX /utf-8 /std:c++17 /EHsc /MT /DKK_ADAPTER_CONTRACT_TEST "'+$source+'" /Fo"'+$out+'\contract.obj" /Fe"'+$out+'\contract.exe" && "'+$out+'\contract.exe"'
Push-Location -LiteralPath $out
try{
    & $env:ComSpec /d /c $command
    if($LASTEXITCODE){throw 'Adapter build or contract failed'}
}finally{Pop-Location}
@{status='built-and-contract-tested';architecture='i686';msvc='14.36';dll=(Join-Path $out 'kk_native_cloud_ticket.dll');original_modules_loaded=$false;vm_tested=$false;auto_inject=$false}|ConvertTo-Json
