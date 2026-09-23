#pragma once
#include "socket_io.hpp"
namespace kknet {
static bool installed=false;
struct CoverageDiagnostic {DWORD size=20,version=1,reason=0,module=0,location=0;};
static CoverageDiagnostic coverage_diagnostic;
inline bool coverage_failure(DWORD reason,size_t location=0){coverage_diagnostic.reason=reason;coverage_diagnostic.location=static_cast<DWORD>(location);return false;}
// Relay-only policy: recognise imported server APIs without exposing a plaintext
// inbound peer path. Never call Winsock, allocate a socket, or modify out args.
inline int WSAAPI listen_denied(SOCKET,int){WSASetLastError(WSAEOPNOTSUPP);return SOCKET_ERROR;}
inline SOCKET WSAAPI accept_denied(SOCKET,sockaddr*,int*){WSASetLastError(WSAEOPNOTSUPP);return INVALID_SOCKET;}
struct Function {const char* name;void* replacement;};
static Function functions[]={
    {"listen",reinterpret_cast<void*>(&listen_denied)},{"accept",reinterpret_cast<void*>(&accept_denied)},
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
    coverage_diagnostic.module=static_cast<DWORD>(reinterpret_cast<uintptr_t>(module));
    kkhooks::Image image{reinterpret_cast<unsigned char*>(module),extent};
    IMAGE_DOS_HEADER dos{};IMAGE_NT_HEADERS32 nt{};
    if(!image.get(0,dos)||dos.e_magic!=IMAGE_DOS_SIGNATURE||dos.e_lfanew<0||
       !image.get(static_cast<size_t>(dos.e_lfanew),nt)||nt.Signature!=IMAGE_NT_SIGNATURE||
       nt.FileHeader.Machine!=IMAGE_FILE_MACHINE_I386||nt.OptionalHeader.Magic!=IMAGE_NT_OPTIONAL_HDR32_MAGIC||
       nt.FileHeader.SizeOfOptionalHeader<sizeof(IMAGE_OPTIONAL_HEADER32)||
       nt.OptionalHeader.NumberOfRvaAndSizes<=IMAGE_DIRECTORY_ENTRY_IMPORT)return coverage_failure(1);
    auto dir=nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if(!dir.VirtualAddress)return true;
    if(dir.Size<sizeof(IMAGE_IMPORT_DESCRIPTOR)||!image.range(dir.VirtualAddress,dir.Size))return coverage_failure(2,dir.VirtualAddress);
    for(size_t at=dir.VirtualAddress;at+sizeof(IMAGE_IMPORT_DESCRIPTOR)<=size_t(dir.VirtualAddress)+dir.Size;at+=sizeof(IMAGE_IMPORT_DESCRIPTOR)){
        IMAGE_IMPORT_DESCRIPTOR entry{};if(!image.get(at,entry))return coverage_failure(3,at);if(!entry.Name)return true;
        char name[512];if(!image.text(entry.Name,name,sizeof(name)))return coverage_failure(4,entry.Name);
        bool kernel=!_stricmp(name,"kernel32.dll")||!_stricmp(name,"kernelbase.dll");
        bool winsock=!_stricmp(name,"WS2_32.dll")||!_stricmp(name,"WSOCK32.dll");
        if(!_stricmp(name,"MSWSOCK.dll")){++kkhooks::unsupported;return coverage_failure(5,at);}
        if(!kernel&&!winsock)continue;
        HMODULE dependency=GetModuleHandleA(name);if(!dependency)return coverage_failure(6,at);
        if(!entry.FirstThunk||!entry.OriginalFirstThunk){++kkhooks::unsupported;return coverage_failure(7,at);}
        bool ended=false;
        for(size_t slot_at=entry.FirstThunk;image.range(slot_at,sizeof(void*));slot_at+=sizeof(void*)){
            void* actual=nullptr;if(!image.get(slot_at,actual))return coverage_failure(8,slot_at);if(!actual){ended=true;break;}
            auto slot=reinterpret_cast<void**>(image.base+slot_at);bool known=false;
            IMAGE_THUNK_DATA32 thunk{};
            if(!image.get(size_t(entry.OriginalFirstThunk)+(slot_at-entry.FirstThunk),thunk))return coverage_failure(9,slot_at);
            FARPROC expected=nullptr;
            if(IMAGE_SNAP_BY_ORDINAL32(thunk.u1.Ordinal))expected=GetProcAddress(dependency,MAKEINTRESOURCEA(IMAGE_ORDINAL32(thunk.u1.Ordinal)));
            else{char symbol[512];if(!image.text(size_t(thunk.u1.AddressOfData)+sizeof(WORD),symbol,sizeof(symbol)))return coverage_failure(10,slot_at);expected=GetProcAddress(dependency,symbol);}
            if(!expected)return coverage_failure(11,slot_at);
            for(const auto& p:kkhooks::committed)if(p.slot==slot){if(actual!=p.replacement)return coverage_failure(12,slot_at);known=true;break;}
            for(const auto& p:plan)if(p.slot==slot){if(actual!=p.original)return coverage_failure(13,slot_at);known=true;break;}
            if(known)continue;
            if(actual!=reinterpret_cast<void*>(expected))return coverage_failure(14,slot_at);
            void* replacement=nullptr;
            if(kernel){if(actual==reinterpret_cast<void*>(&GetProcAddress))replacement=reinterpret_cast<void*>(&lookup_hook);}
            else {replacement=resolve(dependency,actual);if(!replacement){++kkhooks::unsupported;return coverage_failure(15,slot_at);}}
            if(replacement&&replacement!=actual&&!kkhooks::add(plan,slot,actual,replacement))return coverage_failure(16,slot_at);
            if(plan.size()+kkhooks::committed.size()>4096)return coverage_failure(17,slot_at);
        }
        if(!ended)return coverage_failure(18,entry.FirstThunk);
    }
    return coverage_failure(19,dir.VirtualAddress);
}
inline bool collect(std::vector<kkhooks::Patch>& plan){
    coverage_diagnostic=CoverageDiagnostic{};
    if(!kkhooks::validate())return coverage_failure(20);
    wchar_t root[32768];DWORD size=GetModuleFileNameW(nullptr,root,32768);if(!size||size==32768)return coverage_failure(21);
    wchar_t* last=wcsrchr(root,L'\\');if(!last)return coverage_failure(22);last[1]=0;size_t length=wcslen(root);
    HMODULE own=nullptr;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,reinterpret_cast<LPCWSTR>(&send_hook),&own))return coverage_failure(23,GetLastError());FreeLibrary(own);
    HANDLE h=kkhooks::module_snapshot([]{return CreateToolhelp32Snapshot(TH32CS_SNAPMODULE,GetCurrentProcessId());},[]{return GetLastError();},[](DWORD ms){Sleep(ms);});
    if(h==INVALID_HANDLE_VALUE)return coverage_failure(24,GetLastError());
    MODULEENTRY32W e{};e.dwSize=sizeof(e);BOOL more=Module32FirstW(h,&e);bool ok=more!=0;if(!ok)coverage_failure(25,GetLastError());
    while(more&&ok){
        if(e.hModule!=own&&(_wcsnicmp(e.szExePath,root,length)==0||e.hModule==GetModuleHandleW(L"SDLogin.dll")||e.hModule==GetModuleHandleW(L"SDP2P.dll"))){
            HMODULE pin=nullptr;
            ok=GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(e.hModule),&pin)!=0;
            if(ok)ok=collect_module(e.hModule,e.modBaseSize,plan);else coverage_failure(26,GetLastError());
        }
        more=Module32NextW(h,&e);
    }
    CloseHandle(h);return ok;
}
}
