// Owned model only: no game files, process injection or network access.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <wchar.h>
#include <string.h>
typedef DWORD (WINAPI *Begin)(void*);
int wmain(int argc,wchar_t** argv){
 wchar_t log[MAX_PATH];wchar_t* slash;char text[4096];DWORD start;HMODULE dll;Begin begin;
 if(argc!=2||wcslen(argv[1])>=MAX_PATH)return 10;
 wcscpy(log,argv[1]);slash=wcsrchr(log,L'\\');
 if(!slash)return 11;
 slash[1]=0;
 if(wcslen(log)+wcslen(L"kk1-official-flow.log")>=MAX_PATH)return 12;
 wcscat(log,L"kk1-official-flow.log");
 // Use a fresh build directory; do not accept a previous run's log.
 if(GetFileAttributesW(log)!=INVALID_FILE_ATTRIBUTES)return 13;
 dll=LoadLibraryW(argv[1]);if(!dll)return 14;
 begin=(Begin)(void*)GetProcAddress(dll,"KkOfficialFlowBegin");if(!begin)return 15;
 if(begin(NULL)!=ERROR_NOT_READY)return 16;
 start=GetTickCount();
 while(GetTickCount()-start<5000){
  FILE* file=_wfopen(log,L"rb");
  if(file){
   size_t count=fread(text,1,sizeof(text)-1,file);fclose(file);text[count]=0;
   if(strstr(text,"identity_rejected")){
    if(!strstr(text," loaded pid=")||strstr(text,"sdo_enter")||strstr(text,"roleprop_ready"))return 17;
    if(begin(NULL)!=ERROR_NOT_READY)return 18;
    puts("PASS: undecorated manual export, module-relative log, unrelated process rejected; no original client loaded.");
    // Production initializer lives until process exit; do not unload its callbacks.
    return 0;
   }
  }
  Sleep(20);
 }
 return 19;
}
