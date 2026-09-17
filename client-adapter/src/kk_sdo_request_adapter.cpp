// Isolated SDK request-boundary adapter. No fake success, driver bypass,
// game-state writes, instruction patches, or original file changes.
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <wininet.h>
#include <string>
using Connect=HINTERNET(WINAPI*)(HINTERNET,LPCSTR,INTERNET_PORT,LPCSTR,LPCSTR,DWORD,DWORD,DWORD_PTR);
using Open=HINTERNET(WINAPI*)(HINTERNET,LPCSTR,LPCSTR,LPCSTR,LPCSTR,LPCSTR*,DWORD,DWORD_PTR);
static Connect original_connect;
static Open original_open;
static HINTERNET WINAPI local_connect(HINTERNET h,LPCSTR,INTERNET_PORT,LPCSTR user,LPCSTR password,DWORD service,DWORD flags,DWORD_PTR context){
    return original_connect(h,"127.0.0.1",18082,user,password,service,flags,context);
}
static HINTERNET WINAPI local_open(HINTERNET h,LPCSTR method,LPCSTR path,LPCSTR version,LPCSTR ref,LPCSTR* accept,DWORD flags,DWORD_PTR context){
    std::string normalized=path?path:"";
    if(normalized.compare(0,7,"authen/")==0)normalized="/"+normalized;
    return original_open(h,method,normalized.c_str(),version,ref,accept,flags&~INTERNET_FLAG_SECURE,context);
}
using GetKey=int (__stdcall*)(void*);
using Password=void* (__thiscall*)(void*,void*,const char*,int);
using Assign=void (__thiscall*)(void*,const char*);
static GetKey original_key;
static Password original_password;
static BYTE own_key[258];
static BYTE* sdk;
static BYTE* dui;
static void receipt(const char* stage){
    FILE* f=nullptr;fopen_s(&f,"C:\\KK-Lab\\sdo-original-window-20260913-1931\\adapter.log","ab");
    if(f){fprintf(f,"pid=%lu stage=%s\n",GetCurrentProcessId(),stage);fclose(f);}
}
static int __stdcall local_key(void* client){
    // SdoBaseClient 2.2.2.0 constructor/endpoint parser: strings+4/+36,
    // ports+32/+64. Called synchronously before its worker queues getGuid.
    __try {
        if(!client)return -10130005;
        auto assign=(Assign)(sdk+(0x100054B0-0x10000000));
        assign((BYTE*)client+4,"127.0.0.1");assign((BYTE*)client+36,"127.0.0.1");
        *(DWORD*)((BYTE*)client+32)=18082;*(DWORD*)((BYTE*)client+64)=18082;
        receipt("local_endpoints_applied");
    }__except(EXCEPTION_EXECUTE_HANDLER){receipt("endpoint_adapter_failed");return -10130005;}
    return original_key(client);
}
static void* __fastcall local_password(void* self,void*,void* result,const char* key,int length){
    __try {
        BYTE* control=*(BYTE**)((BYTE*)self+1276);
        if(control&&*(DWORD*)(control+92)==1){
            BYTE* store=*(BYTE**)(control+104);
            if(store&&*(uintptr_t*)store==(uintptr_t)dui+(0x100B6814-0x10000000)){
                memcpy(store+40,own_key,258);receipt("own_key_applied_to_password_store");
            }else receipt("store_identity_not_matched");
        }else receipt("user_mode_store_not_ready");
    }__except(EXCEPTION_EXECUTE_HANDLER){receipt("password_adapter_failed");}
    return original_password(self,result,key,length);
}
static bool replace_import(BYTE* module,const char* dll,unsigned ordinal,const char* named,void* replacement,void** old){
    auto dos=(IMAGE_DOS_HEADER*)module;auto nt=(IMAGE_NT_HEADERS*)(module+dos->e_lfanew);
    auto dir=nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for(auto d=(IMAGE_IMPORT_DESCRIPTOR*)(module+dir.VirtualAddress);d->Name;++d){
        if(_stricmp((char*)module+d->Name,dll))continue;
        if(!d->OriginalFirstThunk)return false;
        auto names=(IMAGE_THUNK_DATA32*)(module+d->OriginalFirstThunk);auto slots=(IMAGE_THUNK_DATA32*)(module+d->FirstThunk);
        for(unsigned i=0;names[i].u1.AddressOfData;++i){
            bool match=IMAGE_SNAP_BY_ORDINAL32(names[i].u1.Ordinal)?(ordinal&&IMAGE_ORDINAL32(names[i].u1.Ordinal)==ordinal):
                (named&&strcmp((char*)((IMAGE_IMPORT_BY_NAME*)(module+names[i].u1.AddressOfData))->Name,named)==0);
            if(!match)continue;
            DWORD protection;if(!VirtualProtect(&slots[i].u1.Function,4,PAGE_READWRITE,&protection))return false;
            *old=(void*)slots[i].u1.Function;slots[i].u1.Function=(DWORD)(uintptr_t)replacement;
            DWORD ignored;VirtualProtect(&slots[i].u1.Function,4,protection,&ignored);return true;
        }
    }return false;
}
static DWORD WINAPI install(void*){
    wchar_t image[32768];GetModuleFileNameW(nullptr,image,32768);
    if(wcsstr(image,L"C:\\KK-Lab\\sdo-native-client-20260913-1931\\sdo\\sdologin\\sdologin.exe")!=image &&
       wcsstr(image,L"C:\\KK-Lab\\sdo-native-client-20260913-1931\\SDO\\sdologin\\sdologin.exe")!=image){receipt("image_rejected");return 1;}
    HANDLE f=CreateFileW(L"C:\\KK-Lab\\sdo-original-window-20260913-1931\\active-public-key.bin",GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
    DWORD read=0;if(f==INVALID_HANDLE_VALUE)return 2;
    BOOL ok=ReadFile(f,own_key,258,&read,nullptr);CloseHandle(f);if(!ok||read!=258||*(WORD*)own_key!=1024)return 3;
    BYTE* exe=(BYTE*)GetModuleHandleW(nullptr);sdk=(BYTE*)GetModuleHandleW(L"SdoBaseClient.dll");dui=(BYTE*)GetModuleHandleW(L"duilib.dll");
    if(!sdk||!dui||exe!=(BYTE*)0x400000){receipt("modules_not_ready");return 4;}
    if(GetProcAddress((HMODULE)sdk,MAKEINTRESOURCEA(16))!=(FARPROC)(sdk+0xC1B0)){receipt("sdk_identity_rejected");return 5;}
    const char* name="?GetPasswordText@CSdoEditUI@DuiLib@@QAE?AVCStdString@2@QBDH@Z";
    bool p=replace_import(exe,"duilib.dll",0,name,(void*)local_password,(void**)&original_password);
    bool k=p&&replace_import(exe,"SdoBaseClient.dll",16,nullptr,(void*)local_key,(void**)&original_key);
    if(p&&!k){void* discarded=nullptr;replace_import(exe,"duilib.dll",0,name,(void*)original_password,&discarded);}
    bool c=k&&replace_import(sdk,"WININET.dll",0,"InternetConnectA",(void*)local_connect,(void**)&original_connect);
    bool o=c&&replace_import(sdk,"WININET.dll",0,"HttpOpenRequestA",(void*)local_open,(void**)&original_open);
    if(!o){void* ignored=nullptr;
        if(c)replace_import(sdk,"WININET.dll",0,"InternetConnectA",(void*)original_connect,&ignored);
        if(k)replace_import(exe,"SdoBaseClient.dll",16,nullptr,(void*)original_key,&ignored);
        if(p&&k)replace_import(exe,"duilib.dll",0,name,(void*)original_password,&ignored);
    }
    receipt(o?"ready":"import_adapter_failed");return o?0:6;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){
    if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(h);HANDLE t=CreateThread(nullptr,0,install,nullptr,0,nullptr);if(t)CloseHandle(t);}return TRUE;
}
