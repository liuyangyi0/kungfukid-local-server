// Narrow HTTP path-only observer. Never records the query string or credentials.
#include <windows.h>
#include <wininet.h>
#include <cstdio>
#include <cstring>
using Open=HINTERNET(WINAPI*)(HINTERNET,LPCSTR,LPCSTR,LPCSTR,LPCSTR,LPCSTR*,DWORD,DWORD_PTR);
static Open original;
static HINTERNET WINAPI observe(HINTERNET c,LPCSTR verb,LPCSTR path,LPCSTR version,LPCSTR referer,LPCSTR* accept,DWORD flags,DWORD_PTR context){
    char name[128]={};unsigned n=0;
    if(path)for(;n<127&&path[n]&&path[n]!='?';++n){char ch=path[n];if(!((ch>='A'&&ch<='Z')||(ch>='a'&&ch<='z')||(ch>='0'&&ch<='9')||ch=='/'||ch=='.'||ch=='_'))break;name[n]=ch;}
    FILE* f=nullptr;fopen_s(&f,"C:\\KK-Lab\\sdo-original-window-20260913-1931\\http-path.log","ab");if(f){fprintf(f,"pid=%lu tick=%lu path=%s\n",GetCurrentProcessId(),GetTickCount(),name);fclose(f);}
    return original(c,verb,path,version,referer,accept,flags,context);
}
static DWORD WINAPI install(void*){
    wchar_t image[32768];GetModuleFileNameW(nullptr,image,32768);
    if(_wcsicmp(image,L"C:\\KK-Lab\\sdo-native-client-20260913-1931\\sdo\\sdologin\\sdologin.exe"))return 1;
    BYTE* m=(BYTE*)GetModuleHandleW(L"SdoBaseClient.dll");if(!m)return 2;
    auto nt=(IMAGE_NT_HEADERS*)(m+((IMAGE_DOS_HEADER*)m)->e_lfanew);
    auto d=(IMAGE_IMPORT_DESCRIPTOR*)(m+nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for(;d->Name;++d){
        if(_stricmp((char*)m+d->Name,"WININET.dll"))continue;
        if(!d->OriginalFirstThunk)return 3;
        auto names=(IMAGE_THUNK_DATA32*)(m+d->OriginalFirstThunk);auto slots=(IMAGE_THUNK_DATA32*)(m+d->FirstThunk);
        for(unsigned i=0;names[i].u1.AddressOfData;++i){
            if(IMAGE_SNAP_BY_ORDINAL32(names[i].u1.Ordinal))continue;
            if(strcmp((char*)((IMAGE_IMPORT_BY_NAME*)(m+names[i].u1.AddressOfData))->Name,"HttpOpenRequestA"))continue;
            DWORD protect;if(!VirtualProtect(&slots[i].u1.Function,4,PAGE_READWRITE,&protect))return 4;
            original=(Open)slots[i].u1.Function;slots[i].u1.Function=(DWORD)observe;
            DWORD ignored;VirtualProtect(&slots[i].u1.Function,4,protect,&ignored);return 0;
        }
    }return 5;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(h);HANDLE t=CreateThread(nullptr,0,install,nullptr,0,nullptr);if(t)CloseHandle(t);}return TRUE;}
