#pragma once
#include "socket_io.hpp"
namespace kknet {
static bool installed=false;
struct Function {const char* name;void* replacement;};
static Function functions[]={
    {"ioctlsocket",reinterpret_cast<void*>(&ioctls_hook)},
    {"send",reinterpret_cast<void*>(&send_hook)},{"recv",reinterpret_cast<void*>(&recv_hook)},
    {"sendto",reinterpret_cast<void*>(&sendto_hook)},{"recvfrom",reinterpret_cast<void*>(&recvfrom_hook)},
    {"closesocket",reinterpret_cast<void*>(&close_hook)},{"select",reinterpret_cast<void*>(&select_hook)},
    {"WSAAsyncSelect",reinterpret_cast<void*>(&async_select)},{"WSAEventSelect",reinterpret_cast<void*>(&event_select)},
    {"WSAEnumNetworkEvents",reinterpret_cast<void*>(&enum_events)},
    {"WSASend",reinterpret_cast<void*>(&send_buffers)},{"WSARecv",reinterpret_cast<void*>(&recv_buffers)},
    {"WSASendTo",reinterpret_cast<void*>(&sendto_buffers)},{"WSARecvFrom",reinterpret_cast<void*>(&recvfrom_buffers)}};
static const char* safe_functions[]={"socket","connect","WSAConnect","bind","getpeername","getsockname","setsockopt","getsockopt","ioctlsocket","shutdown","htons","htonl","ntohs","ntohl","gethostbyname","gethostbyaddr","inet_addr","inet_ntoa","WSAStartup","WSACleanup","WSAGetLastError","WSASetLastError","WSAAsyncSelect","WSAEventSelect","WSACreateEvent","WSACloseEvent","WSAWaitForMultipleEvents","WSAEnumNetworkEvents","WSAResetEvent","WSASetEvent","WSASocketA","WSASocketW","__WSAFDIsSet","WSAFDIsSet","gethostname","WSAIsBlocking","WSASetBlockingHook","WSAUnhookBlockingHook","WSACancelBlockingCall"};
inline void* resolve(HMODULE module,void* real){
    for(const auto& f:functions)if(real==reinterpret_cast<void*>(GetProcAddress(module,f.name)))return f.replacement;
    for(auto name:safe_functions)if(real==reinterpret_cast<void*>(GetProcAddress(module,name)))return real;
    return nullptr;
}
inline FARPROC WINAPI lookup_hook(HMODULE module,LPCSTR name){
    auto actual=GetProcAddress(module,name);if(!actual)return nullptr;
    if(module!=GetModuleHandleW(L"WS2_32.dll")&&module!=GetModuleHandleW(L"WSOCK32.dll"))return actual;
    auto result=resolve(module,reinterpret_cast<void*>(actual));if(!result)SetLastError(ERROR_PROC_NOT_FOUND);
    return reinterpret_cast<FARPROC>(result);
}
// Plan only: no VirtualProtect/CAS or live slot writes in this function.
inline bool collect_module(HMODULE module,size_t extent,std::vector<kkhooks::Patch>& plan){
    kkhooks::Image image{reinterpret_cast<unsigned char*>(module),extent};
    IMAGE_DOS_HEADER dos{};IMAGE_NT_HEADERS32 nt{};
    if(!image.get(0,dos)||dos.e_magic!=IMAGE_DOS_SIGNATURE||dos.e_lfanew<0||
       !image.get(static_cast<size_t>(dos.e_lfanew),nt)||nt.Signature!=IMAGE_NT_SIGNATURE||
       nt.FileHeader.Machine!=IMAGE_FILE_MACHINE_I386||nt.OptionalHeader.Magic!=IMAGE_NT_OPTIONAL_HDR32_MAGIC||
       nt.FileHeader.SizeOfOptionalHeader<sizeof(IMAGE_OPTIONAL_HEADER32)||
       nt.OptionalHeader.NumberOfRvaAndSizes<=IMAGE_DIRECTORY_ENTRY_IMPORT)return false;
    auto dir=nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if(!dir.VirtualAddress)return true;
    if(dir.Size<sizeof(IMAGE_IMPORT_DESCRIPTOR)||!image.range(dir.VirtualAddress,dir.Size))return false;
    for(size_t at=dir.VirtualAddress;at+sizeof(IMAGE_IMPORT_DESCRIPTOR)<=size_t(dir.VirtualAddress)+dir.Size;at+=sizeof(IMAGE_IMPORT_DESCRIPTOR)){
        IMAGE_IMPORT_DESCRIPTOR entry{};if(!image.get(at,entry))return false;if(!entry.Name)return true;
        char name[512];if(!image.text(entry.Name,name,sizeof(name)))return false;
        bool kernel=!_stricmp(name,"kernel32.dll")||!_stricmp(name,"kernelbase.dll");
        bool winsock=!_stricmp(name,"WS2_32.dll")||!_stricmp(name,"WSOCK32.dll");
        if(!_stricmp(name,"MSWSOCK.dll")){++kkhooks::unsupported;return false;}
        if(!kernel&&!winsock)continue;
        HMODULE dependency=GetModuleHandleA(name);if(!dependency)return false;
        if(!entry.FirstThunk||!entry.OriginalFirstThunk){++kkhooks::unsupported;return false;}
        bool ended=false;
        for(size_t slot_at=entry.FirstThunk;image.range(slot_at,sizeof(void*));slot_at+=sizeof(void*)){
            void* actual=nullptr;if(!image.get(slot_at,actual))return false;if(!actual){ended=true;break;}
            auto slot=reinterpret_cast<void**>(image.base+slot_at);bool known=false;
            IMAGE_THUNK_DATA32 thunk{};
            if(!image.get(size_t(entry.OriginalFirstThunk)+(slot_at-entry.FirstThunk),thunk))return false;
            FARPROC expected=nullptr;
            if(IMAGE_SNAP_BY_ORDINAL32(thunk.u1.Ordinal))expected=GetProcAddress(dependency,MAKEINTRESOURCEA(IMAGE_ORDINAL32(thunk.u1.Ordinal)));
            else{char symbol[512];if(!image.text(size_t(thunk.u1.AddressOfData)+sizeof(WORD),symbol,sizeof(symbol)))return false;expected=GetProcAddress(dependency,symbol);}
            if(!expected)return false;
            for(const auto& p:kkhooks::committed)if(p.slot==slot){if(actual!=p.replacement)return false;known=true;break;}
            for(const auto& p:plan)if(p.slot==slot){if(actual!=p.original)return false;known=true;break;}
            if(known)continue;
            if(actual!=reinterpret_cast<void*>(expected))return false;
            void* replacement=nullptr;
            if(kernel){if(actual==reinterpret_cast<void*>(&GetProcAddress))replacement=reinterpret_cast<void*>(&lookup_hook);}
            else {replacement=resolve(dependency,actual);if(!replacement){++kkhooks::unsupported;return false;}}
            if(replacement&&replacement!=actual&&!kkhooks::add(plan,slot,actual,replacement))return false;
            if(plan.size()+kkhooks::committed.size()>4096)return false;
        }
        if(!ended)return false;
    }
    return false;
}
inline bool collect(std::vector<kkhooks::Patch>& plan){
    if(!kkhooks::validate())return false;
    wchar_t root[32768];DWORD size=GetModuleFileNameW(nullptr,root,32768);if(!size||size==32768)return false;
    wchar_t* last=wcsrchr(root,L'\\');if(!last)return false;last[1]=0;size_t length=wcslen(root);
    HMODULE own=nullptr;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,reinterpret_cast<LPCWSTR>(&send_hook),&own))return false;FreeLibrary(own);
    HANDLE h=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE,GetCurrentProcessId());if(h==INVALID_HANDLE_VALUE)return false;
    MODULEENTRY32W e{};e.dwSize=sizeof(e);BOOL more=Module32FirstW(h,&e);bool ok=more!=0;
    while(more&&ok){
        if(e.hModule!=own&&(_wcsnicmp(e.szExePath,root,length)==0||e.hModule==GetModuleHandleW(L"SDLogin.dll")||e.hModule==GetModuleHandleW(L"SDP2P.dll"))){
            HMODULE pin=nullptr;
            ok=GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(e.hModule),&pin)!=0;
            if(ok)ok=collect_module(e.hModule,e.modBaseSize,plan);
        }
        more=Module32NextW(h,&e);
    }
    CloseHandle(h);return ok;
}
}
