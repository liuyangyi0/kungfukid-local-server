@echo off
setlocal
where cl.exe >nul 2>nul
if errorlevel 1 (
  echo Run from an x86 Visual Studio Native Tools command prompt.
  exit /b 1
)
if /i not "%VSCMD_ARG_TGT_ARCH%"=="x86" (
  echo x86 compiler environment required.
  exit /b 1
)
pushd "%~dp0.."
if not exist build\adapter mkdir build\adapter
cl /nologo /std:c++17 /EHsc /MT /LD client-adapter\src\kk_sdo_request_adapter.cpp /Fobuild\adapter\request.obj /Febuild\adapter\request-adapter.dll /link user32.lib wininet.lib
if errorlevel 1 goto failed
cl /nologo /std:c++17 /EHsc /MT /LD client-adapter\src\kk_sdo_input_provider.cpp /Fobuild\adapter\input.obj /Febuild\adapter\input-provider-a07.dll /link user32.lib
if errorlevel 1 goto failed
cl /nologo /std:c++17 /EHsc /MT /LD client-adapter\src\kk_sdo_http_path_probe.cpp /Fobuild\adapter\path.obj /Febuild\adapter\http-path-a07.dll /link user32.lib wininet.lib
if errorlevel 1 goto failed
cl /nologo /std:c++17 /EHsc /MT /LD client-adapter\src\kk_roleprop_observer.cpp /Fobuild\adapter\observer.obj /Febuild\adapter\observer.dll /link user32.lib
if errorlevel 1 goto failed
popd
exit /b 0
:failed
popd
exit /b 1
