#pragma once
#include "socket_io.hpp"
namespace kknet {
struct Patch {void** slot;void* original;void* replacement;};
static std::vector<Patch> patches;
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
// IAT-only adaptation of currently loaded client-tree modules. No code bytes
// are changed. Unsupported Winsock/overlapped APIs fail closed, not bypass.
inline bool install(){
    for(const auto& patch:patches)if(*patch.slot!=patch.replacement)return false;
    wchar_t root[32768];DWORD root_size=GetModuleFileNameW(nullptr,root,32768);if(!root_size||root_size==32768)return false;
    wchar_t* last=wcsrchr(root,L'\\');if(!last)return false;last[1]=0;size_t root_length=wcslen(root);
    HMODULE own=nullptr;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,reinterpret_cast<LPCWSTR>(&send_hook),&own))return false;
    FreeLibrary(own); // balance lookup; Install has already pinned the adapter
    HANDLE snapshot_modules=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE,GetCurrentProcessId());if(snapshot_modules==INVALID_HANDLE_VALUE)return false;
    std::vector<HMODULE> modules;MODULEENTRY32W entry{};entry.dwSize=sizeof(entry);
    BOOL more=Module32FirstW(snapshot_modules,&entry);
    while(more){if(entry.hModule!=own&&(_wcsnicmp(entry.szExePath,root,root_length)==0||entry.hModule==GetModuleHandleW(L"SDLogin.dll")||entry.hModule==GetModuleHandleW(L"SDP2P.dll"))){
        HMODULE pinned=nullptr;
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(entry.hModule),&pinned)){CloseHandle(snapshot_modules);return false;}
        modules.push_back(entry.hModule);
    }more=Module32NextW(snapshot_modules,&entry);}
    CloseHandle(snapshot_modules);
    for(auto module:modules){auto base=reinterpret_cast<unsigned char*>(module);auto dos=reinterpret_cast<IMAGE_DOS_HEADER*>(base);
        if(dos->e_magic!=IMAGE_DOS_SIGNATURE)return false;auto nt=reinterpret_cast<IMAGE_NT_HEADERS32*>(base+dos->e_lfanew);
        if(nt->Signature!=IMAGE_NT_SIGNATURE||nt->OptionalHeader.Magic!=IMAGE_NT_OPTIONAL_HDR32_MAGIC)return false;
        auto dir=nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];if(!dir.VirtualAddress)continue;
        auto imports=reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base+dir.VirtualAddress);
        for(;imports->Name;imports++){
            const char* name=reinterpret_cast<char*>(base+imports->Name);
            if(!_stricmp(name,"MSWSOCK.dll"))return false;
            if(!_stricmp(name,"kernel32.dll")||!_stricmp(name,"kernelbase.dll")){
                auto slot=reinterpret_cast<void**>(base+imports->FirstThunk);
                for(;*slot;slot++)if(*slot==reinterpret_cast<void*>(&GetProcAddress)){
                    auto target=reinterpret_cast<void*>(&lookup_hook);if(!exchange_slot(slot,*slot,target))return false;
                    patches.push_back({slot,reinterpret_cast<void*>(&GetProcAddress),target});
                }
                continue;
            }
            if(_stricmp(name,"WS2_32.dll")&&_stricmp(name,"WSOCK32.dll"))continue;
            HMODULE winsock=GetModuleHandleA(name);if(!winsock)return false;
            auto slot=reinterpret_cast<void**>(base+imports->FirstThunk);
            for(;*slot;slot++){
              if(slot==send_slot||slot==close_slot)continue;
              bool known=false;for(const auto& p:patches)if(p.slot==slot){known=true;break;}
              if(known)continue;
              if(!resolve(winsock,*slot))return false; // unknown network API: no silent bypass
              for(const auto& f:functions){
                auto expected=reinterpret_cast<void*>(GetProcAddress(winsock,f.name));if(!expected||*slot!=expected)continue;
                // SDLogin's two existing hooks prepend the admission prefix and
                // delegate to these encrypted primitives. Preserve those slots.
                if(slot==send_slot||slot==close_slot)continue;
                HMODULE pinned=nullptr;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(module),&pinned))return false;
                if(!exchange_slot(slot,expected,f.replacement))return false;
                patches.push_back({slot,expected,f.replacement});break;
              }
            }
        }
    }
    installed=true;return true;
}
}
