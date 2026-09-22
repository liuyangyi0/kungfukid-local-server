// Own-service authentication adapter. No proxy/listener or battle hooks.
// Reuses the SDK's existing socket for a one-time admission prefix only.
// Native interfaces verified against SDLogin E18EE649... and SDP2P FF9FCDE2...
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <bcrypt.h>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cctype>
#include <array>
#pragma comment(lib,"bcrypt.lib")
#pragma comment(lib,"ws2_32.lib")
#pragma comment(lib,"user32.lib")
static_assert(sizeof(void*)==4,"Original SDK interfaces are i686 only");

#pragma pack(push,1)
struct TicketConfig {
    DWORD size,version,pid,valid_for_ms;
    char account[21];
    unsigned char tcp[32];
    char udp_hex[65];
    unsigned char sdk[32];
    DWORD sdk_ipv4;
    unsigned short sdk_port;
    unsigned char transport_id[16],transport_key[32];
    unsigned short game_port,udp_port;
};
struct AdapterStatus {DWORD size,installed,configured,active_calls;};
#pragma pack(pop)
static SRWLOCK state_lock=SRWLOCK_INIT;
static TicketConfig ticket{};
static ULONGLONG deadline=0;
static volatile LONG active_calls=0;
static SRWLOCK send_lock=SRWLOCK_INIT;
static ULONGLONG generation=0;
using SendFn=int (WSAAPI*)(SOCKET,const char*,int,int);
using CloseFn=int (WSAAPI*)(SOCKET);
using PeerFn=int (WSAAPI*)(SOCKET,sockaddr*,int*);
static SendFn original_send=nullptr;
static CloseFn original_close=nullptr;
static PeerFn peer_name=&getpeername;
static void** send_slot=nullptr;
static void** close_slot=nullptr;
struct SdkSocket {SOCKET socket=INVALID_SOCKET;ULONGLONG generation=0;int sent=0;};
static std::array<SdkSocket,16> sdk_sockets{};
struct CallScope {CallScope(){InterlockedIncrement(&active_calls);}~CallScope(){InterlockedDecrement(&active_calls);}};
using CredentialFn=int (__stdcall*)(void*,void*,int);
using P2PLoginFn=unsigned char (__thiscall*)(void*,const void*);
static CredentialFn original_credential=nullptr;
static P2PLoginFn original_p2p_login=nullptr;
static void** credential_slot=nullptr;
static void** p2p_slot=nullptr;

static bool valid(const TicketConfig& c){
    if(c.size!=sizeof(c)||c.version!=3||c.pid!=GetCurrentProcessId()||c.valid_for_ms==0||c.valid_for_ms>28800000||!c.sdk_ipv4||!c.sdk_port||!c.game_port||!c.udp_port||c.game_port==c.sdk_port)return false;
    size_t n=0;while(n<sizeof(c.account)&&c.account[n]){const char ch=c.account[n];if(!((ch>='a'&&ch<='z')||(ch>='A'&&ch<='Z')||(ch>='0'&&ch<='9')))return false;++n;}
    if(n<3||n>20||c.udp_hex[64]!=0)return false;
    unsigned int present=0;for(unsigned char b:c.tcp)present|=b;
    if(!present)return false;
    present=0;for(unsigned char b:c.sdk)present|=b;if(!present)return false;
    present=0;for(unsigned char b:c.transport_key)present|=b;if(!present)return false;
    present=0;for(unsigned char b:c.transport_id)present|=b;if(!present)return false;
    for(size_t i=0;i<64;i++)if(!((c.udp_hex[i]>='0'&&c.udp_hex[i]<='9')||(c.udp_hex[i]>='a'&&c.udp_hex[i]<='f')))return false;
    return true;
}
static bool snapshot(TicketConfig& result){
    AcquireSRWLockShared(&state_lock);
    const bool ready=deadline>GetTickCount64();
    if(ready)result=ticket;
    ReleaseSRWLockShared(&state_lock);return ready;
}
static bool exchange_slot(void** slot,void* expected,void* replacement);
#include "socket_transport.hpp"
static int WSAAPI sdk_send_hook(SOCKET socket,const char* data,int length,int flags){
    CallScope scope;TicketConfig c{};ULONGLONG epoch=0;
    AcquireSRWLockShared(&state_lock);
    const bool ready=deadline>GetTickCount64();if(ready){c=ticket;epoch=generation;}
    ReleaseSRWLockShared(&state_lock);
    if(!ready)return original_send?original_send(socket,data,length,flags):SOCKET_ERROR;
    sockaddr_in peer{};int size=sizeof(peer);
    if(length<0||flags||peer_name(socket,reinterpret_cast<sockaddr*>(&peer),&size)||peer.sin_family!=AF_INET||peer.sin_addr.s_addr!=c.sdk_ipv4||ntohs(peer.sin_port)!=c.sdk_port){SecureZeroMemory(&c,sizeof(c));WSASetLastError(WSAEACCES);return SOCKET_ERROR;}
    if(!length){SecureZeroMemory(&c,sizeof(c));return 0;}
    AcquireSRWLockExclusive(&send_lock);
    SdkSocket* state=nullptr;
    for(auto& item:sdk_sockets)if(item.socket==socket){state=&item;break;}
    if(!state)for(auto& item:sdk_sockets)if(item.socket==INVALID_SOCKET){item={socket,epoch,0};state=&item;break;}
    int result=SOCKET_ERROR,error=0;
    if(!state)error=WSAENOBUFS;
    else if(state->generation!=epoch)error=WSAECONNRESET;
    else{
        unsigned char prefix[36]={'K','K','S','1'};std::memcpy(prefix+4,c.sdk,32);
        while(state->sent<36){
            int count=original_send(socket,reinterpret_cast<const char*>(prefix)+state->sent,36-state->sent,0);
            if(count==SOCKET_ERROR){error=WSAGetLastError();break;}
            if(count<=0){error=WSAECONNRESET;break;}
            state->sent+=count;
        }
        SecureZeroMemory(prefix,sizeof(prefix));
        // Return counts only for the caller's original bytes. A partial prefix
        // remains resumable after WSAEWOULDBLOCK without consuming native data.
        if(!error){result=original_send(socket,data,length,flags);if(result==SOCKET_ERROR)error=WSAGetLastError();}
    }
    ReleaseSRWLockExclusive(&send_lock);SecureZeroMemory(&c,sizeof(c));
    if(error)WSASetLastError(error);return result;
}
static int WSAAPI sdk_close_hook(SOCKET socket){
    CallScope scope;AcquireSRWLockExclusive(&send_lock);
    for(auto& item:sdk_sockets)if(item.socket==socket)item={};
    ReleaseSRWLockExclusive(&send_lock);
    return original_close?original_close(socket):SOCKET_ERROR;
}
static bool tcp_bytes(const TicketConfig& c,void* target,size_t length){
    if(length!=32||!target)return false;
    SIZE_T written=0;
    return WriteProcessMemory(GetCurrentProcess(),target,c.tcp,32,&written)&&written==32;
}
static bool udp_parameters(const TicketConfig& c,const void* source,std::array<unsigned char,162>& result){
    SIZE_T got=0;
    if(!source||!ReadProcessMemory(GetCurrentProcess(),source,result.data(),result.size(),&got)||got!=result.size())return false;
    // Native login config: address+0, port+4, value+8, name21 at+12,
    // user-data129 at+33;100035F0 copies the latter synchronously.
    std::memset(result.data()+12,0,21);std::memcpy(result.data()+12,c.account,std::strlen(c.account));
    std::memset(result.data()+33,0,129);std::memcpy(result.data()+33,"KKN1:",5);
    std::memcpy(result.data()+38,c.udp_hex,64);return true;
}
static int __stdcall credential_hook(void* self,void* output,int length){
    InterlockedIncrement(&active_calls);TicketConfig c{};int result=-1;
    if(snapshot(c)&&length==32)result=tcp_bytes(c,output,static_cast<size_t>(length))?32:-1;
    else if(original_credential)result=original_credential(self,output,length);
    SecureZeroMemory(&c,sizeof(c));InterlockedDecrement(&active_calls);return result;
}
static unsigned char __fastcall p2p_hook(void* self,void*,const void* parameters){
    InterlockedIncrement(&active_calls);TicketConfig c{};unsigned char result=0;
    if(snapshot(c)){
        std::array<unsigned char,162> local{};
        if(udp_parameters(c,parameters,local)&&original_p2p_login)result=original_p2p_login(self,local.data());
        SecureZeroMemory(local.data(),local.size());
    }else if(original_p2p_login)result=original_p2p_login(self,parameters);
    SecureZeroMemory(&c,sizeof(c));InterlockedDecrement(&active_calls);return result;
}
static bool module_hash(HMODULE module,const unsigned char expected[32]){
    wchar_t path[32768];DWORD n=GetModuleFileNameW(module,path,32768);if(!n||n==32768)return false;
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return false;
    BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;unsigned char digest[32]{};bool ok=false;
    if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0&&BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)>=0){
        std::array<unsigned char,65536> buffer{};DWORD count=0;bool read=true;
        while(true){if(!ReadFile(file,buffer.data(),static_cast<DWORD>(buffer.size()),&count,nullptr)){read=false;break;}if(!count)break;if(BCryptHashData(hash,buffer.data(),count,0)<0){read=false;break;}}
        if(read&&BCryptFinishHash(hash,digest,sizeof(digest),0)>=0)ok=std::memcmp(digest,expected,32)==0;
    }
    if(hash)BCryptDestroyHash(hash);if(alg)BCryptCloseAlgorithmProvider(alg,0);CloseHandle(file);return ok;
}
static bool exchange_slot(void** slot,void* expected,void* replacement){
    if(reinterpret_cast<uintptr_t>(slot)%4)return false;
    DWORD old=0;if(!VirtualProtect(slot,sizeof(void*),PAGE_READWRITE,&old))return false;
    void* prior=InterlockedCompareExchangePointer(slot,replacement,expected);
    DWORD ignored=0;bool protected_ok=VirtualProtect(slot,sizeof(void*),old,&ignored)!=0;
    if(!protected_ok&&prior==expected){
        InterlockedCompareExchangePointer(slot,expected,replacement);
        VirtualProtect(slot,sizeof(void*),old,&ignored);
    }
    return prior==expected&&protected_ok;
}
extern "C" __declspec(dllexport) DWORD WINAPI KkNativeCloudSetTicket(const TicketConfig* input){
    TicketConfig copy{};SIZE_T got=0;
    if(!input||!ReadProcessMemory(GetCurrentProcess(),input,&copy,sizeof(copy),&got)||got!=sizeof(copy)||!valid(copy)){SecureZeroMemory(&copy,sizeof(copy));return ERROR_INVALID_DATA;}
    AcquireSRWLockExclusive(&state_lock);
    // Never reset record counters under a reused key. New login = new process.
    if(generation){ReleaseSRWLockExclusive(&state_lock);SecureZeroMemory(&copy,sizeof(copy));return ERROR_ALREADY_INITIALIZED;}
    ticket=copy;deadline=GetTickCount64()+copy.valid_for_ms;++generation;ReleaseSRWLockExclusive(&state_lock);
    SecureZeroMemory(&copy,sizeof(copy));return ERROR_SUCCESS;
}
extern "C" __declspec(dllexport) DWORD WINAPI KkNativeCloudClearTicket(void*){
    AcquireSRWLockExclusive(&state_lock);SecureZeroMemory(&ticket,sizeof(ticket));deadline=0;ReleaseSRWLockExclusive(&state_lock);return ERROR_SUCCESS;
}
extern "C" __declspec(dllexport) DWORD WINAPI KkNativeCloudInstall(void*){
    if(InterlockedCompareExchange(&active_calls,0,0))return ERROR_BUSY;
    static const unsigned char login_hash[32]={0xe1,0x8e,0xe6,0x49,0xb6,0xfd,0x57,0x13,0x45,0xff,0x04,0xf9,0x7c,0xb0,0x80,0x26,0x33,0x56,0xdf,0x2c,0x6e,0x5d,0x58,0x61,0x69,0x56,0x50,0xf2,0xa3,0x52,0x1b,0x4a};
    static const unsigned char p2p_hash[32]={0xff,0x9f,0xcd,0xe2,0x20,0x01,0x11,0x09,0x8c,0x87,0x91,0xe8,0x94,0x4f,0x74,0x04,0xed,0x07,0x06,0x8a,0x70,0x3a,0xec,0x8c,0xe6,0xa2,0x4a,0x65,0xd7,0xaf,0x30,0xd0};
    HMODULE login=GetModuleHandleW(L"SDLogin.dll"),p2p=GetModuleHandleW(L"SDP2P.dll");
    if(!login)return ERROR_MOD_NOT_FOUND;
    HMODULE pinned=nullptr;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(&credential_hook),&pinned))return GetLastError();
    if(!credential_slot&&!send_slot&&!close_slot){
        if(!module_hash(login,login_hash))return ERROR_REVISION_MISMATCH;
        HMODULE winsock=GetModuleHandleW(L"WS2_32.dll");if(!winsock)return ERROR_MOD_NOT_FOUND;
        auto sf=reinterpret_cast<SendFn>(GetProcAddress(winsock,"send"));
        auto cf=reinterpret_cast<CloseFn>(GetProcAddress(winsock,"closesocket"));
        auto lf=reinterpret_cast<CredentialFn>(reinterpret_cast<unsigned char*>(login)+0x2CE0);
        if(!sf||!cf)return ERROR_PROC_NOT_FOUND;
        auto ls=reinterpret_cast<void**>(reinterpret_cast<unsigned char*>(login)+0x263F0+0x24);
        auto ss=reinterpret_cast<void**>(reinterpret_cast<unsigned char*>(login)+0x261A0);
        auto cs=reinterpret_cast<void**>(reinterpret_cast<unsigned char*>(login)+0x26194);
        if(*ls!=reinterpret_cast<void*>(lf)||*ss!=reinterpret_cast<void*>(sf)||*cs!=reinterpret_cast<void*>(cf))return ERROR_REVISION_MISMATCH;
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(login),&pinned))return GetLastError();
        original_credential=lf;original_send=&kknet::send_hook;original_close=&kknet::close_hook;
        void** slots[3]={ls,cs,ss};
        void* originals[3]={reinterpret_cast<void*>(lf),reinterpret_cast<void*>(cf),reinterpret_cast<void*>(sf)};
        void* replacements[3]={reinterpret_cast<void*>(credential_hook),reinterpret_cast<void*>(sdk_close_hook),reinterpret_cast<void*>(sdk_send_hook)};
        int installed=0;
        for(;installed<3;installed++)if(!exchange_slot(slots[installed],originals[installed],replacements[installed]))break;
        if(installed!=3){
            for(int i=installed-1;i>=0;i--)exchange_slot(slots[i],replacements[i],originals[i]);
            credential_slot=*ls==replacements[0]?ls:nullptr;
            close_slot=*cs==replacements[1]?cs:nullptr;send_slot=*ss==replacements[2]?ss:nullptr;
            return ERROR_WRITE_FAULT;
        }
        credential_slot=ls;close_slot=cs;send_slot=ss;
    }else if(!credential_slot||!send_slot||!close_slot||
              *credential_slot!=reinterpret_cast<void*>(credential_hook)||
              *send_slot!=reinterpret_cast<void*>(sdk_send_hook)||
              *close_slot!=reinterpret_cast<void*>(sdk_close_hook))return ERROR_INVALID_STATE;
    // SDK can initialize before SDP is loaded. The launcher requires mask13
    // before SDK login, then mask15 before releasing the role/profile gate.
    if(p2p&&!p2p_slot){
        if(!module_hash(p2p,p2p_hash))return ERROR_REVISION_MISMATCH;
        auto ps=reinterpret_cast<void**>(reinterpret_cast<unsigned char*>(p2p)+0x2AE40+6*sizeof(void*));
        auto pf=reinterpret_cast<P2PLoginFn>(reinterpret_cast<unsigned char*>(p2p)+0x6AD0);
        if(*ps!=reinterpret_cast<void*>(pf))return ERROR_REVISION_MISMATCH;
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(p2p),&pinned))return GetLastError();
        original_p2p_login=pf;
        if(!exchange_slot(ps,reinterpret_cast<void*>(pf),reinterpret_cast<void*>(p2p_hook)))return ERROR_WRITE_FAULT;
        p2p_slot=ps;
    }else if(p2p_slot&&*p2p_slot!=reinterpret_cast<void*>(p2p_hook))return ERROR_INVALID_STATE;
    if(!kknet::install())return ERROR_WRITE_FAULT;
    return ERROR_SUCCESS;
}
extern "C" __declspec(dllexport) DWORD WINAPI KkNativeCloudRemove(void*){
    KkNativeCloudClearTicket(nullptr);
    kknet::retire();
    bool ok=true;
    if(send_slot){ok=exchange_slot(send_slot,reinterpret_cast<void*>(sdk_send_hook),reinterpret_cast<void*>(original_send));if(ok)send_slot=nullptr;}
    if(close_slot){bool removed=exchange_slot(close_slot,reinterpret_cast<void*>(sdk_close_hook),reinterpret_cast<void*>(original_close));ok=ok&&removed;if(removed)close_slot=nullptr;}
    if(p2p_slot){bool removed=exchange_slot(p2p_slot,reinterpret_cast<void*>(p2p_hook),reinterpret_cast<void*>(original_p2p_login));ok=ok&&removed;if(removed)p2p_slot=nullptr;}
    if(credential_slot){bool removed=exchange_slot(credential_slot,reinterpret_cast<void*>(credential_hook),reinterpret_cast<void*>(original_credential));ok=ok&&removed;if(removed)credential_slot=nullptr;}
    return ok?ERROR_SUCCESS:ERROR_WRITE_FAULT;
}
extern "C" __declspec(dllexport) DWORD WINAPI KkNativeCloudGetStatus(AdapterStatus* output){
    TicketConfig c{};AdapterStatus s{sizeof(AdapterStatus),(credential_slot?1u:0u)|(p2p_slot?2u:0u)|(send_slot?4u:0u)|(close_slot?8u:0u)|(kknet::installed?16u:0u),snapshot(c)?1u:0u,static_cast<DWORD>(InterlockedCompareExchange(&active_calls,0,0))};SecureZeroMemory(&c,sizeof(c));
    SIZE_T written=0;return output&&WriteProcessMemory(GetCurrentProcess(),output,&s,sizeof(s),&written)&&written==sizeof(s)?ERROR_SUCCESS:ERROR_INVALID_PARAMETER;
}
#ifndef KK_ADAPTER_CONTRACT_TEST
BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID){if(reason==DLL_PROCESS_ATTACH)DisableThreadLibraryCalls(module);return TRUE;}
#else
#include <cassert>
#include <vector>
static std::vector<char> sent_bytes;
static int send_step=0;
static int WSAAPI fake_send(SOCKET,const char* data,int size,int){
    if(send_step++==1){WSASetLastError(WSAEWOULDBLOCK);return SOCKET_ERROR;}
    int count=send_step==1?13:size;sent_bytes.insert(sent_bytes.end(),data,data+count);return count;
}
static int WSAAPI fake_peer(SOCKET,sockaddr* address,int* size){auto p=reinterpret_cast<sockaddr_in*>(address);p->sin_family=AF_INET;p->sin_addr.s_addr=0x0100007f;p->sin_port=htons(18000);*size=sizeof(sockaddr_in);return 0;}
static int WSAAPI fake_close(SOCKET){return 0;}
static unsigned int partial_calls=0;
static int WSAAPI fragment_send(SOCKET s,const char* data,int count,int flags){
    if((partial_calls++%3)==1){WSASetLastError(WSAEWOULDBLOCK);return SOCKET_ERROR;}
    return ::send(s,data,(std::min)(count,701),flags);
}
static std::array<unsigned char,162> forwarded{};
static unsigned char __fastcall fake_p2p(void* self,void*,const void* parameters){
    assert(self==reinterpret_cast<void*>(0x1234));std::memcpy(forwarded.data(),parameters,forwarded.size());return 7;
}
static int __stdcall fake_credential(void* self,void*,int length){assert(self==reinterpret_cast<void*>(0x1234));return length+1;}
int main(int argc,char** argv){
    if(argc==3&&std::strcmp(argv[1],"--host-adapter")==0){
        HMODULE module=LoadLibraryA(argv[2]);if(!module)return 2;
        wchar_t name[96];swprintf_s(name,L"Local\\KkNativeModel-%lu",GetCurrentProcessId());
        HANDLE stop=CreateEventW(nullptr,TRUE,FALSE,name);if(!stop)return 3;
        puts("READY");fflush(stdout);WaitForSingleObject(stop,30000);CloseHandle(stop);FreeLibrary(module);return 0;
    }
    WSADATA ws{};assert(WSAStartup(MAKEWORD(2,2),&ws)==0);
    TicketConfig c{};c.size=sizeof(c);c.version=3;c.pid=GetCurrentProcessId();c.valid_for_ms=10000;strcpy_s(c.account,"AdapterTest");
    std::memset(c.sdk,0x39,32);c.sdk_ipv4=0x0100007f;c.sdk_port=18000;
    c.game_port=18001;c.udp_port=18001;std::memset(c.transport_id,1,16);std::memset(c.transport_key,2,32);
    std::memset(c.tcp,0x5a,32);std::memset(c.udp_hex,'a',64);
    if(argc==4&&std::strcmp(argv[1],"--encrypted-echo")==0){
        int port=std::atoi(argv[2]),udp_port=std::atoi(argv[3]);assert(port>1024&&port<65535&&udp_port>1024&&udp_port<65535);
        c.game_port=static_cast<unsigned short>(port);c.sdk_port=static_cast<unsigned short>(port-1);c.udp_port=static_cast<unsigned short>(udp_port);
        assert(KkNativeCloudSetTicket(&c)==0);
        SOCKET s=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);assert(s!=INVALID_SOCKET);
        sockaddr_in peer{};peer.sin_family=AF_INET;peer.sin_addr.s_addr=c.sdk_ipv4;peer.sin_port=htons(c.game_port);
        assert(connect(s,reinterpret_cast<sockaddr*>(&peer),sizeof(peer))==0);
        kknet::raw_send=&fragment_send;
        u_long nonblocking=1;assert(ioctlsocket(s,FIONBIO,&nonblocking)==0);
        std::vector<char> input(100000);for(size_t i=0;i<input.size();i++)input[i]=static_cast<char>(i%251);
        size_t sent=0,received=0;ULONGLONG end=GetTickCount64()+10000;
        while(sent<input.size()){
            assert(GetTickCount64()<end);int n=kknet::send_hook(s,input.data()+sent,static_cast<int>(input.size()-sent),0);
            if(n==SOCKET_ERROR){assert(WSAGetLastError()==WSAEWOULDBLOCK);Sleep(1);}else{assert(n>0);sent+=n;}
        }
        WSAEVENT event=WSACreateEvent();assert(event!=WSA_INVALID_EVENT);assert(kknet::event_select(s,event,FD_READ|FD_CLOSE)==0);
        unsigned int buffered_events=0;
        while(received<input.size()){
            assert(GetTickCount64()<end);fd_set ready;FD_ZERO(&ready);FD_SET(s,&ready);timeval wait{0,10000};int selected=kknet::select_hook(0,&ready,nullptr,nullptr,&wait);assert(selected>=0);if(!selected)continue;
            char data[17];int n=kknet::recv_hook(s,data,sizeof(data),0);
            if(n==SOCKET_ERROR){assert(WSAGetLastError()==WSAEWOULDBLOCK);continue;}
            assert(n>0&&received+static_cast<size_t>(n)<=input.size());assert(!std::memcmp(data,input.data()+received,n));received+=n;
            auto existing=kknet::state(s,false);assert(existing);
            if(existing->plain_at<existing->plain.size()){
                assert(WaitForSingleObject(event,0)==WAIT_OBJECT_0);WSANETWORKEVENTS events{};
                assert(kknet::enum_events(s,event,&events)==0&&(events.lNetworkEvents&FD_READ));++buffered_events;
                char peek[17];int count=kknet::recv_hook(s,peek,sizeof(peek),MSG_PEEK);assert(count>0&&!std::memcmp(peek,input.data()+received,count));
            }
        }
        assert(buffered_events>100&&partial_calls>100);assert(kknet::close_hook(s)==0);WSACloseEvent(event);
        SOCKET u=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);assert(u!=INVALID_SOCKET);peer.sin_port=htons(c.udp_port);
        assert(kknet::sendto_hook(u,"udp",3,0,reinterpret_cast<sockaddr*>(&peer),sizeof(peer))==3);
        DWORD timeout=3000;assert(setsockopt(u,SOL_SOCKET,SO_RCVTIMEO,reinterpret_cast<char*>(&timeout),sizeof(timeout))==0);
        char result[32];sockaddr_in from{};int from_size=sizeof(from);
        assert(kknet::recvfrom_hook(u,result,sizeof(result),0,reinterpret_cast<sockaddr*>(&from),&from_size)==3&&!std::memcmp(result,"udp",3));
        peer.sin_port=htons(static_cast<u_short>(c.udp_port-1));assert(kknet::sendto_hook(u,"denied",6,0,reinterpret_cast<sockaddr*>(&peer),sizeof(peer))==SOCKET_ERROR&&WSAGetLastError()==WSAEACCES);
        kknet::close_hook(u);kknet::retire();assert(KkNativeCloudSetTicket(&c)==ERROR_ALREADY_INITIALIZED);
        WSACleanup();puts("PASS: CNG/Python real encrypted TCP and UDP;100000-byte stream;17-byte reads; buffered select; foreign endpoint refused.");return 0;
    }
    assert(KkNativeCloudSetTicket(&c)==0);
    static_assert(sizeof(TicketConfig)==224,"C# handoff wire size");
    unsigned char cid[16]{};cid[0]=7;
    kkrecord::Records a,b;assert(a.init(c.transport_key,c.transport_id,cid,2,false));assert(b.init(c.transport_key,c.transport_id,cid,2,true));
    std::vector<unsigned char> encrypted,plain;
    assert(a.seal(reinterpret_cast<const unsigned char*>("hello"),5,encrypted));
    for(unsigned char ch:encrypted)printf("%02x",ch);puts("");
    auto corrupt=encrypted;corrupt.back()^=1;assert(!b.open(corrupt.data(),corrupt.size(),plain));
    assert(b.open(encrypted.data(),encrypted.size(),plain)&&plain.size()==5&&!std::memcmp(plain.data(),"hello",5));
    assert(!b.open(encrypted.data(),encrypted.size(),plain));
    assert(b.seal(reinterpret_cast<const unsigned char*>("reply"),5,encrypted));assert(a.open(encrypted.data(),encrypted.size(),plain));
    kkrecord::Records u,v;assert(u.init(c.transport_key,c.transport_id,cid,3,false));assert(v.init(c.transport_key,c.transport_id,cid,3,true));
    std::vector<unsigned char> first,second;assert(u.seal(reinterpret_cast<const unsigned char*>("1"),1,first));assert(u.seal(reinterpret_cast<const unsigned char*>("2"),1,second));
    assert(v.open(second.data(),second.size(),plain));assert(v.open(first.data(),first.size(),plain));assert(!v.open(first.data(),first.size(),plain));
    original_send=&fake_send;original_close=&fake_close;peer_name=&fake_peer;
    assert(sdk_send_hook(7,"native",6,0)==SOCKET_ERROR&&WSAGetLastError()==WSAEWOULDBLOCK);
    assert(sent_bytes.size()==13);assert(sdk_send_hook(7,"native",6,0)==6);
    assert(sent_bytes.size()==42&&std::memcmp(sent_bytes.data(),"KKS1",4)==0&&std::memcmp(sent_bytes.data()+36,"native",6)==0);
    assert(sdk_send_hook(7,"next",4,0)==4&&sent_bytes.size()==46);
    assert(sdk_close_hook(7)==0);assert(sdk_send_hook(7,"reuse",5,0)==5&&sent_bytes.size()==87);
    std::array<unsigned char,40> out{};out.fill(0xcc);
    assert(credential_hook(nullptr,out.data()+4,32)==32);
    for(size_t i=0;i<out.size();++i)assert(out[i]==(i>=4&&i<36?0x5a:0xcc));
    assert(credential_hook(nullptr,out.data()+4,31)==-1);
    std::array<unsigned char,162> source{},changed{};source.fill(0x67);
    assert(udp_parameters(c,source.data(),changed));
    assert(std::memcmp(source.data(),changed.data(),12)==0);
    assert(std::memcmp(changed.data()+12,"AdapterTest",11)==0);
    assert(std::memcmp(changed.data()+33,"KKN1:",5)==0);
    for(size_t i=38;i<102;i++)assert(changed[i]=='a');
    for(size_t i=102;i<162;i++)assert(changed[i]==0);
    assert(source[33]==0x67);
    original_p2p_login=reinterpret_cast<P2PLoginFn>(&fake_p2p);
    assert(p2p_hook(reinterpret_cast<void*>(0x1234),nullptr,source.data())==7);
    assert(forwarded==changed);assert(source[33]==0x67);
    original_credential=&fake_credential;
    assert(credential_hook(reinterpret_cast<void*>(0x1234),nullptr,128)==129);
    original_credential=nullptr;
    c.udp_hex[3]='!';assert(KkNativeCloudSetTicket(&c)==ERROR_INVALID_DATA);
    c.udp_hex[3]='a';c.pid++;assert(KkNativeCloudSetTicket(&c)==ERROR_INVALID_DATA);c.pid--;
    c.valid_for_ms=0;assert(KkNativeCloudSetTicket(&c)==ERROR_INVALID_DATA);
    assert(!udp_parameters(c,nullptr,changed));
    deadline=0;assert(credential_hook(nullptr,out.data()+4,32)==-1);
    assert(KkNativeCloudInstall(nullptr)==ERROR_MOD_NOT_FOUND);
    assert(KkNativeCloudClearTicket(nullptr)==0);assert(credential_hook(nullptr,out.data()+4,32)==-1);
    original_credential=&fake_credential;assert(credential_hook(reinterpret_cast<void*>(0x1234),nullptr,32)==33);original_credential=nullptr;
    AdapterStatus status{};assert(KkNativeCloudGetStatus(&status)==0);assert(status.installed==0&&status.configured==0&&status.active_calls==0);
    puts("PASS: i686 credential ABI, exact32-byte write, bounded162-byte SDP config copy, expiry/config rejection and inactive loading. No original module loaded.");
    WSACleanup();
}
#endif
