// In-process record adapter on existing sockets. No listener or forwarding proxy.
#pragma once
#include "record_crypto.hpp"
#include <memory>
#include <map>
#include <atomic>
#include <tlhelp32.h>
namespace kknet {
static auto raw_send=&::send;static auto raw_recv=&::recv;
static auto raw_sendto=&::sendto;static auto raw_recvfrom=&::recvfrom;
static auto raw_close=&::closesocket;static auto raw_select=&::select;
struct SocketState {
    kkrecord::Records records;ULONGLONG epoch=0;SRWLOCK tx=SRWLOCK_INIT,rx=SRWLOCK_INIT;
    std::vector<unsigned char> pending,original,wire,plain;size_t offset=0,plain_at=0;
    std::atomic<bool> dead{false};
    ~SocketState(){if(!original.empty())SecureZeroMemory(original.data(),original.size());if(!plain.empty())SecureZeroMemory(plain.data(),plain.size());}
};
static SRWLOCK sockets_lock=SRWLOCK_INIT;
static std::map<SOCKET,std::shared_ptr<SocketState>> sockets;
struct Notification {HWND window=nullptr;u_int message=0;WSAEVENT event=WSA_INVALID_EVENT;long mask=0;};
static std::map<SOCKET,Notification> notifications;
// One UDP association per grant; socket recreation must obtain a fresh grant.
static std::shared_ptr<SocketState> udp_state;
static bool retired=false;
struct Guard {SRWLOCK* lock;explicit Guard(SRWLOCK& l):lock(&l){AcquireSRWLockExclusive(lock);}~Guard(){ReleaseSRWLockExclusive(lock);}};
inline int error(int code){WSASetLastError(code);return SOCKET_ERROR;}
inline void readable(SOCKET s){
    Notification n;{Guard lock(sockets_lock);auto it=notifications.find(s);if(it==notifications.end())return;n=it->second;}
    if(!(n.mask&FD_READ))return;
    if(n.window)PostMessageW(n.window,n.message,static_cast<WPARAM>(s),WSAMAKESELECTREPLY(FD_READ,0));
    if(n.event!=WSA_INVALID_EVENT)WSASetEvent(n.event);
}
inline bool endpoint(const sockaddr* address,int size,const TicketConfig& c,bool udp,unsigned char& channel){
    if(!address||size!=sizeof(sockaddr_in)||address->sa_family!=AF_INET)return false;
    auto a=reinterpret_cast<const sockaddr_in*>(address);if(a->sin_addr.s_addr!=c.sdk_ipv4)return false;
    auto port=ntohs(a->sin_port);
    channel=udp?3:(port==c.sdk_port?1:2);
    return udp?port==c.udp_port:(port==c.sdk_port||port==c.game_port);
}
inline std::shared_ptr<SocketState> state(SOCKET s,bool udp,const sockaddr* supplied=nullptr,int size=0){
    TicketConfig c{};ULONGLONG epoch=0;
    AcquireSRWLockShared(&state_lock);bool ready=deadline>GetTickCount64();if(ready){c=ticket;epoch=generation;}ReleaseSRWLockShared(&state_lock);
    if(!ready){WSASetLastError(WSAEACCES);return {};}
    sockaddr_in remote{};int count=sizeof(remote);unsigned char channel=0;
    if(!supplied){if(getpeername(s,reinterpret_cast<sockaddr*>(&remote),&count)){SecureZeroMemory(&c,sizeof(c));return {};}supplied=reinterpret_cast<sockaddr*>(&remote);size=count;}
    if(!endpoint(supplied,size,c,udp,channel)){SecureZeroMemory(&c,sizeof(c));WSASetLastError(WSAEACCES);return {};}
    Guard lock(sockets_lock);std::shared_ptr<SocketState> result;
    if(retired){SecureZeroMemory(&c,sizeof(c));WSASetLastError(WSAECONNRESET);return {};}
    if(udp){result=udp_state;if(!result){result=std::make_shared<SocketState>();udp_state=result;}}
    else {auto found=sockets.find(s);if(found!=sockets.end())result=found->second;else if(sockets.size()<32){result=std::make_shared<SocketState>();sockets.emplace(s,result);}}
    if(result&&!result->records.initialized){
        unsigned char cid[16]{};
        if(!udp&&BCryptGenRandom(nullptr,cid,sizeof(cid),BCRYPT_USE_SYSTEM_PREFERRED_RNG)<0)result->dead=true;
        if(!result->dead&&!result->records.init(c.transport_key,c.transport_id,cid,channel,false))result->dead=true;
        result->epoch=epoch;
    }
    SecureZeroMemory(&c,sizeof(c));
    if(!result||result->dead||result->epoch!=epoch||result->records.channel!=channel){WSASetLastError(WSAECONNRESET);return {};}
    return result;
}
inline int WSAAPI send_hook(SOCKET s,const char* data,int n,int flags){
    CallScope scope;
    try{
        if(n<0||flags||(!data&&n))return error(WSAEINVAL);
        auto p=state(s,false);if(!p)return SOCKET_ERROR;if(!n)return 0;Guard lock(p->tx);
        int used=(std::min)(n,static_cast<int>(kkrecord::max_plain));
        if(p->pending.empty()){
            p->original.assign(reinterpret_cast<const unsigned char*>(data),reinterpret_cast<const unsigned char*>(data)+used);
            if(!p->records.seal(p->original.data(),p->original.size(),p->pending)){p->dead=true;return error(WSAECONNRESET);}p->offset=0;
        }else if(p->original.size()!=static_cast<size_t>(used)||std::memcmp(p->original.data(),data,used)){p->dead=true;return error(WSAECONNRESET);}
        while(p->offset<p->pending.size()){
            int sent=raw_send(s,reinterpret_cast<const char*>(p->pending.data()+p->offset),static_cast<int>(p->pending.size()-p->offset),0);
            if(sent==SOCKET_ERROR){if(WSAGetLastError()!=WSAEWOULDBLOCK)p->dead=true;return SOCKET_ERROR;}
            if(sent<=0){p->dead=true;return error(WSAECONNRESET);}p->offset+=sent;
        }
        SecureZeroMemory(p->original.data(),p->original.size());p->original.clear();p->pending.clear();return used;
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI recv_hook(SOCKET s,char* data,int n,int flags){
    CallScope scope;
    try{
        if(n<0||(flags!=0&&flags!=MSG_PEEK)||(!data&&n))return error(WSAEINVAL);
        auto p=state(s,false);if(!p)return SOCKET_ERROR;if(!n)return 0;Guard lock(p->rx);
        while(p->plain_at==p->plain.size()){
            size_t needed=kkrecord::header_size;
            if(p->wire.size()>=needed){if(!kkrecord::header(p->wire.data(),p->wire.size())){p->dead=true;return error(WSAECONNRESET);}needed+=static_cast<size_t>(kkrecord::number(p->wire.data()+48,4))+16;}
            if(p->wire.size()==needed&&needed>kkrecord::header_size){
                if(!p->records.open(p->wire.data(),p->wire.size(),p->plain)){p->dead=true;return error(WSAECONNRESET);}
                p->plain_at=0;p->wire.clear();break;
            }
            unsigned char buffer[32768];int count=raw_recv(s,reinterpret_cast<char*>(buffer),static_cast<int>((std::min)(sizeof(buffer),needed-p->wire.size())),0);
            if(count==SOCKET_ERROR)return SOCKET_ERROR;
            if(!count){if(!p->wire.empty()){p->dead=true;return error(WSAECONNRESET);}return 0;}
            p->wire.insert(p->wire.end(),buffer,buffer+count);
        }
        int count=static_cast<int>((std::min)(static_cast<size_t>(n),p->plain.size()-p->plain_at));std::memcpy(data,p->plain.data()+p->plain_at,count);if(flags!=MSG_PEEK)p->plain_at+=count;
        if(p->plain_at==p->plain.size()){SecureZeroMemory(p->plain.data(),p->plain.size());p->plain.clear();p->plain_at=0;}
        else readable(s);
        return count;
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI sendto_hook(SOCKET s,const char* data,int n,int flags,const sockaddr* to,int size){
    CallScope scope;
    try{
        if(n<=0||n>kkrecord::max_plain||flags||!data)return error(WSAEMSGSIZE);
        auto p=state(s,true,to,size);if(!p)return SOCKET_ERROR;Guard lock(p->tx);std::vector<unsigned char> encrypted;
        if(!p->records.seal(reinterpret_cast<const unsigned char*>(data),n,encrypted))return error(WSAECONNRESET);
        int sent=raw_sendto(s,reinterpret_cast<const char*>(encrypted.data()),static_cast<int>(encrypted.size()),0,to,size);
        if(sent==SOCKET_ERROR)return sent;return static_cast<size_t>(sent)==encrypted.size()?n:error(WSAECONNRESET);
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI recvfrom_hook(SOCKET s,char* data,int n,int flags,sockaddr* from,int* size){
    CallScope scope;
    try{
        if(n<0||flags||!data||(from&&!size))return error(WSAEINVAL);
        unsigned char wire[kkrecord::header_size+kkrecord::max_plain+16];sockaddr_in peer{};int count=sizeof(peer);
        int got=raw_recvfrom(s,reinterpret_cast<char*>(wire),sizeof(wire),0,reinterpret_cast<sockaddr*>(&peer),&count);if(got==SOCKET_ERROR)return got;
        auto p=state(s,true,reinterpret_cast<sockaddr*>(&peer),count);if(!p)return SOCKET_ERROR;
        Guard lock(p->rx);std::vector<unsigned char> plain;
        if(!p->records.open(wire,got,plain))return error(WSAEWOULDBLOCK); // drop forged/replayed datagrams
        if(plain.size()>static_cast<size_t>(n)||from&&*size<count){SecureZeroMemory(plain.data(),plain.size());return error(WSAEMSGSIZE);}
        std::memcpy(data,plain.data(),plain.size());int result=static_cast<int>(plain.size());SecureZeroMemory(plain.data(),plain.size());
        if(from){std::memcpy(from,&peer,count);*size=count;}return result;
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI close_hook(SOCKET s){CallScope scope;{Guard lock(sockets_lock);sockets.erase(s);notifications.erase(s);}return raw_close(s);}
inline int WSAAPI select_hook(int n,fd_set* read,fd_set* write,fd_set* except,const timeval* timeout){
    // Decrypted leftovers must remain readable even when the kernel queue is empty.
    fd_set buffered{};std::vector<std::pair<SOCKET,std::shared_ptr<SocketState>>> candidates;
    if(read){Guard lock(sockets_lock);for(u_int i=0;i<read->fd_count;i++){auto it=sockets.find(read->fd_array[i]);if(it!=sockets.end())candidates.push_back(*it);}}
    for(const auto& item:candidates){auto p=item.second;AcquireSRWLockShared(&p->rx);bool available=p->plain_at<p->plain.size();ReleaseSRWLockShared(&p->rx);if(available)FD_SET(item.first,&buffered);}
    timeval zero{};int result=raw_select(n,read,write,except,buffered.fd_count?&zero:timeout);if(result<0)return result;
    if(read)for(u_int i=0;i<buffered.fd_count;i++)if(!FD_ISSET(buffered.fd_array[i],read)){FD_SET(buffered.fd_array[i],read);++result;}return result;
}
inline int WSAAPI async_select(SOCKET s,HWND window,u_int message,long events){
    {Guard lock(sockets_lock);if(notifications.size()>=32&&!notifications.count(s))return error(WSAENOBUFS);}
    // Preserve the legacy caller's HWND notification ABI; this is not a new
    // choice of deprecated API for our own network architecture.
#pragma warning(push)
#pragma warning(disable:4996)
    int result=WSAAsyncSelect(s,window,message,events);if(result)return result;
#pragma warning(pop)
    {Guard lock(sockets_lock);if(events)notifications[s]={window,message,WSA_INVALID_EVENT,events};else notifications.erase(s);}return 0;
}
inline int WSAAPI event_select(SOCKET s,WSAEVENT event,long events){
    {Guard lock(sockets_lock);if(notifications.size()>=32&&!notifications.count(s))return error(WSAENOBUFS);}
    int result=WSAEventSelect(s,event,events);if(result)return result;
    {Guard lock(sockets_lock);if(events)notifications[s]={nullptr,0,event,events};else notifications.erase(s);}return 0;
}
inline int WSAAPI enum_events(SOCKET s,WSAEVENT event,LPWSANETWORKEVENTS events){
    int result=WSAEnumNetworkEvents(s,event,events);if(result||!events)return result;
    std::shared_ptr<SocketState> p;bool registered=false;
    {Guard lock(sockets_lock);auto it=sockets.find(s);if(it!=sockets.end())p=it->second;auto n=notifications.find(s);registered=n!=notifications.end()&&(n->second.mask&FD_READ);}
    if(p&&registered){AcquireSRWLockShared(&p->rx);bool available=p->plain_at<p->plain.size();ReleaseSRWLockShared(&p->rx);
        if(available){events->lNetworkEvents|=FD_READ;events->iErrorCode[FD_READ_BIT]=0;}}
    return 0;
}
inline int WSAAPI send_buffers(SOCKET s,LPWSABUF buffers,DWORD count,LPDWORD sent,DWORD flags,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||count!=1||!buffers||!sent)return error(WSAEOPNOTSUPP);
    int result=send_hook(s,buffers[0].buf,static_cast<int>(buffers[0].len),static_cast<int>(flags));if(result<0)return result;*sent=result;return 0;
}
inline int WSAAPI recv_buffers(SOCKET s,LPWSABUF buffers,DWORD count,LPDWORD got,LPDWORD flags,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||count!=1||!buffers||!got||!flags)return error(WSAEOPNOTSUPP);
    int result=recv_hook(s,buffers[0].buf,static_cast<int>(buffers[0].len),static_cast<int>(*flags));if(result<0)return result;*got=result;return 0;
}
inline int WSAAPI sendto_buffers(SOCKET s,LPWSABUF buffers,DWORD count,LPDWORD sent,DWORD flags,const sockaddr* to,int size,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||count!=1||!buffers||!sent)return error(WSAEOPNOTSUPP);
    int result=sendto_hook(s,buffers[0].buf,static_cast<int>(buffers[0].len),static_cast<int>(flags),to,size);if(result<0)return result;*sent=result;return 0;
}
inline int WSAAPI recvfrom_buffers(SOCKET s,LPWSABUF buffers,DWORD count,LPDWORD got,LPDWORD flags,sockaddr* from,int* size,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||count!=1||!buffers||!got||!flags)return error(WSAEOPNOTSUPP);
    int result=recvfrom_hook(s,buffers[0].buf,static_cast<int>(buffers[0].len),static_cast<int>(*flags),from,size);if(result<0)return result;*got=result;return 0;
}
inline void retire(){Guard lock(sockets_lock);retired=true;sockets.clear();notifications.clear();udp_state.reset();}
struct Patch {void** slot;void* original;void* replacement;};
static std::vector<Patch> patches;
static bool installed=false;
struct Function {const char* name;void* replacement;};
static Function functions[]={
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
