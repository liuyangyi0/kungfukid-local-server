// Bounded in-process transport; no listener, additional socket or local proxy.
#pragma once
#include "record_crypto.hpp"
#include <memory>
#include <map>
#include <atomic>
#include <deque>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <tlhelp32.h>
namespace kknet {
static auto raw_send=&::send;static auto raw_recv=&::recv;
static auto raw_sendto=&::sendto;static auto raw_recvfrom=&::recvfrom;
static auto raw_close=&::closesocket;static auto raw_select=&::select;
static std::atomic<bool> traffic_enabled{true}; // lifecycle gate also enforced before any raw I/O
static std::atomic<bool (*)()> coverage_gate{nullptr};
inline bool coverage_valid(){auto gate=coverage_gate.load();return !gate||gate();}
constexpr size_t queue_limit=256*1024;
struct Guard {SRWLOCK* lock;explicit Guard(SRWLOCK& l):lock(&l){AcquireSRWLockExclusive(lock);}~Guard(){ReleaseSRWLockExclusive(lock);}};
struct QueuedRecord {std::vector<unsigned char> wire;size_t offset=0,plain_size=0;};
struct SocketState {
    kkrecord::Records records;ULONGLONG epoch=0;SOCKET socket=INVALID_SOCKET;
    SRWLOCK tx=SRWLOCK_INIT,rx=SRWLOCK_INIT;
    std::mutex queue_lock;std::condition_variable queue_changed;std::deque<std::shared_ptr<QueuedRecord>> queue;
    std::thread worker;std::atomic<bool> dead{false},nonblocking{false},closing{false},accepting{true};
    std::atomic<size_t> queued{0},readable_bytes{0};
    std::vector<unsigned char> wire,plain;size_t plain_at=0;
    ~SocketState(){if(worker.joinable())std::terminate();if(!plain.empty())SecureZeroMemory(plain.data(),plain.size());}
};
static SRWLOCK sockets_lock=SRWLOCK_INIT;
static std::map<SOCKET,std::shared_ptr<SocketState>> sockets;
struct Notification {HWND window=nullptr;u_int message=0;WSAEVENT event=WSA_INVALID_EVENT;long mask=0;};
static std::map<SOCKET,Notification> notifications;
static std::map<SOCKET,bool> modes;
static std::shared_ptr<SocketState> udp_state;
static bool retired=false;
inline int error(int code){WSASetLastError(code);return SOCKET_ERROR;}
inline void notify_socket(SOCKET s,long event,int code=0){
    Notification n;{Guard lock(sockets_lock);auto it=notifications.find(s);if(it==notifications.end())return;n=it->second;}
    if(!(n.mask&event))return;
    if(n.window)PostMessageW(n.window,n.message,static_cast<WPARAM>(s),WSAMAKESELECTREPLY(event,code));
    if(n.event!=WSA_INVALID_EVENT)WSASetEvent(n.event);
}
inline bool ticket_valid(){AcquireSRWLockShared(&state_lock);bool ok=deadline>GetTickCount64();ReleaseSRWLockShared(&state_lock);return ok;}
inline bool ticket_alive(){return ticket_valid()&&traffic_enabled&&coverage_valid();}
inline bool endpoint(const sockaddr* address,int size,const TicketConfig& c,bool udp,unsigned char& channel){
    if(!address||size!=sizeof(sockaddr_in)||address->sa_family!=AF_INET)return false;
    auto a=reinterpret_cast<const sockaddr_in*>(address);if(a->sin_addr.s_addr!=c.sdk_ipv4)return false;
    auto port=ntohs(a->sin_port);channel=udp?3:(port==c.sdk_port?1:2);
    return udp?port==c.udp_port:(port==c.sdk_port||port==c.game_port);
}
inline std::shared_ptr<SocketState> find_state(SOCKET s){Guard lock(sockets_lock);auto it=sockets.find(s);return it==sockets.end()?nullptr:it->second;}
inline std::shared_ptr<SocketState> state(SOCKET s,bool udp,const sockaddr* supplied=nullptr,int size=0){
    TicketConfig c{};ULONGLONG epoch=0;
    AcquireSRWLockShared(&state_lock);bool configured=deadline>GetTickCount64();bool ready=configured&&traffic_enabled&&coverage_valid();if(ready){c=ticket;epoch=generation;}ReleaseSRWLockShared(&state_lock);
    if(!ready){WSASetLastError(configured?WSAEWOULDBLOCK:WSAEACCES);return {};}
    sockaddr_in remote{};int count=sizeof(remote);unsigned char channel=0;
    if(!supplied){if(getpeername(s,reinterpret_cast<sockaddr*>(&remote),&count)){SecureZeroMemory(&c,sizeof(c));return {};}supplied=reinterpret_cast<sockaddr*>(&remote);size=count;}
    if(!endpoint(supplied,size,c,udp,channel)){SecureZeroMemory(&c,sizeof(c));WSASetLastError(WSAEACCES);return {};}
    Guard lock(sockets_lock);std::shared_ptr<SocketState> result;
    if(retired){SecureZeroMemory(&c,sizeof(c));WSASetLastError(WSAECONNRESET);return {};}
    if(udp){result=udp_state;if(!result){result=std::make_shared<SocketState>();udp_state=result;}}
    else{auto it=sockets.find(s);if(it!=sockets.end())result=it->second;else if(sockets.size()<32){result=std::make_shared<SocketState>();sockets.emplace(s,result);}}
    if(result&&!result->records.initialized){
        unsigned char cid[16]{};
        if(!udp&&BCryptGenRandom(nullptr,cid,sizeof(cid),BCRYPT_USE_SYSTEM_PREFERRED_RNG)<0)result->dead=true;
        if(!result->dead&&!result->records.init(c.transport_key,c.transport_id,cid,channel,false))result->dead=true;
        result->epoch=epoch;result->socket=s;auto mode=modes.find(s);if(mode!=modes.end())result->nonblocking=mode->second;
    }
    SecureZeroMemory(&c,sizeof(c));
    if(!result||result->dead||result->closing||result->epoch!=epoch||result->records.channel!=channel){WSASetLastError(WSAECONNRESET);return {};}
    return result;
}
inline void send_worker(SocketState* p){
    try{
        while(true){
            std::shared_ptr<QueuedRecord> record;
            {std::unique_lock<std::mutex> lock(p->queue_lock);p->queue_changed.wait_for(lock,std::chrono::milliseconds(50),[&]{return p->closing||p->dead||!p->queue.empty();});
             if(p->closing||p->dead)break;if(p->queue.empty())continue;record=p->queue.front();}
            if(!ticket_valid()){p->dead=true;break;}
            if(!ticket_alive()){Sleep(10);continue;}
            fd_set writable;FD_ZERO(&writable);FD_SET(p->socket,&writable);timeval timeout{0,20000};
            int ready=raw_select(0,nullptr,&writable,nullptr,&timeout);
            if(ready==SOCKET_ERROR){p->dead=true;break;}if(!ready)continue;
            if(p->closing||!ticket_valid()){p->dead=true;break;}if(!ticket_alive())continue;
            // No queue/global lock is held during socket I/O. Closing first
            // shuts down the owned socket and joins this worker before reuse.
            int n=raw_send(p->socket,reinterpret_cast<const char*>(record->wire.data()+record->offset),static_cast<int>(record->wire.size()-record->offset),0);
            if(n==SOCKET_ERROR){if(WSAGetLastError()==WSAEWOULDBLOCK){Sleep(1);continue;}p->dead=true;break;}
            if(n<=0){p->dead=true;break;}record->offset+=n;
            if(record->offset==record->wire.size()){
                bool was_full;
                {std::lock_guard<std::mutex> lock(p->queue_lock);was_full=p->queued>=queue_limit;p->queue.pop_front();p->queued-=record->plain_size;}
                p->queue_changed.notify_all();if(was_full)notify_socket(p->socket,FD_WRITE);
            }
        }
    }catch(...){p->dead=true;}
    {std::lock_guard<std::mutex> lock(p->queue_lock);p->queue.clear();p->queued=0;}
    p->queue_changed.notify_all();if(p->dead&&!p->closing)notify_socket(p->socket,FD_CLOSE,WSAECONNRESET);
}
inline int WSAAPI send_hook(SOCKET s,const char* data,int n,int flags){
    CallScope scope;
    try{
        if(n<0||flags||(!data&&n))return error(WSAEINVAL);
        auto p=state(s,false);if(!p)return SOCKET_ERROR;if(!n)return 0;
        DWORD timeout=0;int size=sizeof(timeout);if(getsockopt(s,SOL_SOCKET,SO_SNDTIMEO,reinterpret_cast<char*>(&timeout),&size))return SOCKET_ERROR;
        ULONGLONG until=timeout?GetTickCount64()+timeout:0;
        std::unique_lock<std::mutex> lock(p->queue_lock);
        while(p->queued>=queue_limit&&!p->dead&&!p->closing&&p->accepting&&ticket_alive()){
            if(p->nonblocking)return error(WSAEWOULDBLOCK);
            if(until&&GetTickCount64()>=until)return error(WSAETIMEDOUT);
            p->queue_changed.wait_for(lock,std::chrono::milliseconds(until?(std::min)(ULONGLONG(50),until-GetTickCount64()):50));
        }
        if(p->dead||p->closing||!p->accepting||!ticket_alive())return error(WSAECONNRESET);
        if(!p->worker.joinable())p->worker=std::thread(send_worker,p.get());
        size_t accept=(std::min)(static_cast<size_t>(n),queue_limit-p->queued.load()),at=0;
        try{while(at<accept){
            auto record=std::make_shared<QueuedRecord>();record->plain_size=(std::min)(kkrecord::max_plain,accept-at);
            if(!p->records.seal(reinterpret_cast<const unsigned char*>(data)+at,record->plain_size,record->wire))throw std::runtime_error("record seal");
            p->queue.push_back(record);p->queued+=record->plain_size;at+=record->plain_size;
        }}catch(...){p->dead=true;p->queue_changed.notify_all();return error(WSAENOBUFS);}
        lock.unlock();p->queue_changed.notify_all();return static_cast<int>(accept);
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
                if(!p->records.open(p->wire.data(),p->wire.size(),p->plain)){p->dead=true;return error(WSAECONNRESET);}p->plain_at=0;p->wire.clear();break;
            }
            unsigned char buffer[32768];int count=raw_recv(s,reinterpret_cast<char*>(buffer),static_cast<int>((std::min)(sizeof(buffer),needed-p->wire.size())),0);
            if(count==SOCKET_ERROR)return SOCKET_ERROR;
            if(!count){if(!p->wire.empty()){p->dead=true;return error(WSAECONNRESET);}return 0;}
            p->wire.insert(p->wire.end(),buffer,buffer+count);
        }
        int count=static_cast<int>((std::min)(static_cast<size_t>(n),p->plain.size()-p->plain_at));std::memcpy(data,p->plain.data()+p->plain_at,count);if(flags!=MSG_PEEK)p->plain_at+=count;
        p->readable_bytes=p->plain.size()-p->plain_at;
        if(!p->readable_bytes){SecureZeroMemory(p->plain.data(),p->plain.size());p->plain.clear();p->plain_at=0;}else notify_socket(s,FD_READ);
        return count;
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI sendto_hook(SOCKET s,const char* data,int n,int flags,const sockaddr* to,int size){
    CallScope scope;try{
        if(n<=0||n>kkrecord::max_plain||flags||!data)return error(WSAEMSGSIZE);
        auto p=state(s,true,to,size);if(!p)return SOCKET_ERROR;Guard lock(p->tx);std::vector<unsigned char> encrypted;
        if(!p->records.seal(reinterpret_cast<const unsigned char*>(data),n,encrypted))return error(WSAECONNRESET);
        int sent=raw_sendto(s,reinterpret_cast<const char*>(encrypted.data()),static_cast<int>(encrypted.size()),0,to,size);
        if(sent==SOCKET_ERROR)return sent;return static_cast<size_t>(sent)==encrypted.size()?n:error(WSAECONNRESET);
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI recvfrom_hook(SOCKET s,char* data,int n,int flags,sockaddr* from,int* size){
    CallScope scope;try{
        if(n<0||flags||!data||(from&&!size))return error(WSAEINVAL);
        unsigned char wire[kkrecord::header_size+kkrecord::max_plain+16];sockaddr_in peer{};int count=sizeof(peer);
        int got=raw_recvfrom(s,reinterpret_cast<char*>(wire),sizeof(wire),0,reinterpret_cast<sockaddr*>(&peer),&count);if(got==SOCKET_ERROR)return got;
        auto p=state(s,true,reinterpret_cast<sockaddr*>(&peer),count);if(!p)return SOCKET_ERROR;Guard lock(p->rx);std::vector<unsigned char> plain;
        if(!p->records.open(wire,got,plain))return error(WSAEWOULDBLOCK);
        if(plain.size()>static_cast<size_t>(n)||from&&*size<count){SecureZeroMemory(plain.data(),plain.size());return error(WSAEMSGSIZE);}
        std::memcpy(data,plain.data(),plain.size());int result=static_cast<int>(plain.size());SecureZeroMemory(plain.data(),plain.size());if(from){std::memcpy(from,&peer,count);*size=count;}return result;
    }catch(...){return error(WSAENOBUFS);}
}
inline void stop(const std::shared_ptr<SocketState>& p){
    if(!p)return;{std::lock_guard<std::mutex> lock(p->queue_lock);p->closing=true;}
    p->queue_changed.notify_all();::shutdown(p->socket,SD_BOTH);
    // Mutex serializes concurrent retire/close joins, but worker never needs it.
    if(p->worker.joinable())p->worker.join();
}
static std::mutex stop_lock;
inline int WSAAPI close_hook(SOCKET s){
    CallScope scope;std::lock_guard<std::mutex> stopping(stop_lock);auto p=find_state(s);bool timed_out=false;
    if(p){
        linger option{};int size=sizeof(option);if(getsockopt(s,SOL_SOCKET,SO_LINGER,reinterpret_cast<char*>(&option),&size))return SOCKET_ERROR;
        std::unique_lock<std::mutex> lock(p->queue_lock);p->accepting=false;p->queue_changed.notify_all();
        bool abortive=option.l_onoff&&option.l_linger==0;
        if(!abortive&&p->queued&&!p->dead){
            if(option.l_onoff&&p->nonblocking)return error(WSAEWOULDBLOCK);
            auto timeout=std::chrono::milliseconds(option.l_onoff?static_cast<unsigned long long>(option.l_linger)*1000:2000);
            timed_out=!p->queue_changed.wait_for(lock,timeout,[&]{return !p->queued||p->dead;});
        }
    }
    stop(p);{Guard lock(sockets_lock);sockets.erase(s);notifications.erase(s);modes.erase(s);}
    int result=raw_close(s);if(!result&&timed_out)return error(WSAETIMEDOUT);return result;
}
inline int WSAAPI ioctls_hook(SOCKET s,long cmd,u_long* value){
    int result=ioctlsocket(s,cmd,value);if(!result&&cmd==FIONBIO&&value){Guard lock(sockets_lock);if(modes.size()<64||modes.count(s))modes[s]=*value!=0;auto it=sockets.find(s);if(it!=sockets.end())it->second->nonblocking=*value!=0;}return result;
}
inline int WSAAPI select_hook(int n,fd_set* read,fd_set* write,fd_set* except,const timeval* timeout){
    if(timeout&&(timeout->tv_sec<0||timeout->tv_usec<0))return error(WSAEINVAL);
    const fd_set requested_r=read?*read:fd_set{},requested_w=write?*write:fd_set{},requested_e=except?*except:fd_set{};
    if(!requested_r.fd_count&&!requested_w.fd_count&&!requested_e.fd_count)return error(WSAEINVAL);
    ULONGLONG start=GetTickCount64(),duration=timeout?ULONGLONG(timeout->tv_sec)*1000+(timeout->tv_usec+999)/1000:0;
    while(true){
        fd_set r=requested_r,w=requested_w,e=requested_e,vr{},vw{};
        for(u_int i=0;i<requested_r.fd_count;i++){auto p=find_state(requested_r.fd_array[i]);if(p&&(p->readable_bytes||p->dead))FD_SET(p->socket,&vr);}
        for(u_int i=0;i<requested_w.fd_count;i++){auto p=find_state(requested_w.fd_array[i]);if(p){FD_CLR(p->socket,&w);if(!p->dead&&!p->closing&&p->accepting&&p->queued<queue_limit&&ticket_alive())FD_SET(p->socket,&vw);}}
        ULONGLONG elapsed=GetTickCount64()-start;long ms=vr.fd_count||vw.fd_count?0:20;if(timeout)ms=static_cast<long>((std::min)(ULONGLONG(ms),duration>elapsed?duration-elapsed:0));
        timeval wait{0,ms*1000};int result=0;
        if(r.fd_count||w.fd_count||e.fd_count)result=raw_select(n,r.fd_count?&r:nullptr,w.fd_count?&w:nullptr,e.fd_count?&e:nullptr,&wait);else if(ms)Sleep(ms);
        if(result<0)return result;
        for(u_int i=0;i<vr.fd_count;i++)FD_SET(vr.fd_array[i],&r);for(u_int i=0;i<vw.fd_count;i++)FD_SET(vw.fd_array[i],&w);
        result=static_cast<int>(r.fd_count+w.fd_count+e.fd_count);
        if(result||(timeout&&GetTickCount64()-start>=duration)){if(read)*read=r;if(write)*write=w;if(except)*except=e;return result;}
    }
}
inline void remember_mode(SOCKET s){Guard lock(sockets_lock);if(modes.size()<64||modes.count(s))modes[s]=true;auto it=sockets.find(s);if(it!=sockets.end())it->second->nonblocking=true;}
inline int WSAAPI async_select(SOCKET s,HWND window,u_int message,long events){
    {Guard lock(sockets_lock);if(notifications.size()>=32&&!notifications.count(s))return error(WSAENOBUFS);}
#pragma warning(push)
#pragma warning(disable:4996)
    int result=WSAAsyncSelect(s,window,message,events);if(result)return result;
#pragma warning(pop)
    remember_mode(s);{Guard lock(sockets_lock);if(events)notifications[s]={window,message,WSA_INVALID_EVENT,events};else notifications.erase(s);}return 0;
}
inline int WSAAPI event_select(SOCKET s,WSAEVENT event,long events){
    {Guard lock(sockets_lock);if(notifications.size()>=32&&!notifications.count(s))return error(WSAENOBUFS);}
    int result=WSAEventSelect(s,event,events);if(result)return result;remember_mode(s);
    {Guard lock(sockets_lock);if(events)notifications[s]={nullptr,0,event,events};else notifications.erase(s);}return 0;
}
inline int WSAAPI enum_events(SOCKET s,WSAEVENT event,LPWSANETWORKEVENTS events){
    int result=WSAEnumNetworkEvents(s,event,events);if(result||!events)return result;
    auto p=find_state(s);Notification interest;{Guard lock(sockets_lock);auto it=notifications.find(s);if(it!=notifications.end())interest=it->second;}
    if(p){
        if(p->readable_bytes&&(interest.mask&FD_READ)){events->lNetworkEvents|=FD_READ;events->iErrorCode[FD_READ_BIT]=0;}
        if(p->queued>=queue_limit||!p->accepting||!ticket_alive())events->lNetworkEvents&=~FD_WRITE;
        else if(!p->dead&&!p->closing&&p->accepting&&ticket_alive()&&(interest.mask&FD_WRITE)){events->lNetworkEvents|=FD_WRITE;events->iErrorCode[FD_WRITE_BIT]=0;}
        if(p->dead&&(interest.mask&FD_CLOSE)){events->lNetworkEvents|=FD_CLOSE;events->iErrorCode[FD_CLOSE_BIT]=WSAECONNRESET;}
    }return 0;
}
inline bool gather(LPWSABUF buffers,DWORD count,std::vector<char>& data,size_t maximum){
    if(!buffers||!count||count>64)return false;size_t total=0;
    for(DWORD i=0;i<count;i++){if(buffers[i].len&&!buffers[i].buf)return false;total+=(std::min)(static_cast<size_t>(buffers[i].len),maximum-total);if(total==maximum)break;}
    data.reserve(total);for(DWORD i=0;i<count&&data.size()<total;i++){size_t n=(std::min)(static_cast<size_t>(buffers[i].len),total-data.size());if(n)data.insert(data.end(),buffers[i].buf,buffers[i].buf+n);}return true;
}
inline int WSAAPI send_buffers(SOCKET s,LPWSABUF b,DWORD count,LPDWORD sent,DWORD flags,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||!sent)return error(WSAEOPNOTSUPP);*sent=0;
    try{std::vector<char> data;if(!gather(b,count,data,queue_limit))return error(WSAEINVAL);int n=send_hook(s,data.data(),static_cast<int>(data.size()),static_cast<int>(flags));if(!data.empty())SecureZeroMemory(data.data(),data.size());if(n<0)return n;*sent=n;return 0;}catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI recv_buffers(SOCKET s,LPWSABUF b,DWORD count,LPDWORD got,LPDWORD flags,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||!b||!count||count>64||!got||!flags)return error(WSAEOPNOTSUPP);*got=0;
    try{size_t total=0;for(DWORD i=0;i<count;i++){if(b[i].len&&!b[i].buf)return error(WSAEFAULT);total+=(std::min)(static_cast<size_t>(b[i].len),kkrecord::max_plain-total);}
        std::vector<char> data(total);int n=recv_hook(s,data.data(),static_cast<int>(total),static_cast<int>(*flags));if(n<0)return n;size_t at=0;
        for(DWORD i=0;i<count&&at<static_cast<size_t>(n);i++){size_t size=(std::min)(static_cast<size_t>(b[i].len),static_cast<size_t>(n)-at);std::memcpy(b[i].buf,data.data()+at,size);at+=size;}
        if(!data.empty())SecureZeroMemory(data.data(),data.size());*got=n;*flags=0;return 0;
    }catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI sendto_buffers(SOCKET s,LPWSABUF b,DWORD count,LPDWORD sent,DWORD flags,const sockaddr* to,int size,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||!sent)return error(WSAEOPNOTSUPP);*sent=0;
    try{std::vector<char> data;if(!gather(b,count,data,kkrecord::max_plain+1))return error(WSAEINVAL);int n=sendto_hook(s,data.data(),static_cast<int>(data.size()),static_cast<int>(flags),to,size);if(!data.empty())SecureZeroMemory(data.data(),data.size());if(n<0)return n;*sent=n;return 0;}catch(...){return error(WSAENOBUFS);}
}
inline int WSAAPI recvfrom_buffers(SOCKET s,LPWSABUF b,DWORD count,LPDWORD got,LPDWORD flags,sockaddr* from,int* size,LPWSAOVERLAPPED overlap,LPWSAOVERLAPPED_COMPLETION_ROUTINE completion){
    if(overlap||completion||!b||!count||count>64||!got||!flags)return error(WSAEOPNOTSUPP);*got=0;
    try{size_t total=0;for(DWORD i=0;i<count;i++){if(b[i].len&&!b[i].buf)return error(WSAEFAULT);total+=(std::min)(static_cast<size_t>(b[i].len),kkrecord::max_plain-total);}
        std::vector<char> data(total);int n=recvfrom_hook(s,data.data(),static_cast<int>(total),static_cast<int>(*flags),from,size);if(n<0)return n;size_t at=0;
        for(DWORD i=0;i<count&&at<static_cast<size_t>(n);i++){size_t copied=(std::min)(static_cast<size_t>(b[i].len),static_cast<size_t>(n)-at);std::memcpy(b[i].buf,data.data()+at,copied);at+=copied;}
        if(!data.empty())SecureZeroMemory(data.data(),data.size());*got=n;*flags=0;return 0;
    }catch(...){return error(WSAENOBUFS);}
}
inline void retire(){
    std::lock_guard<std::mutex> stopping(stop_lock);std::vector<std::shared_ptr<SocketState>> all;
    {Guard lock(sockets_lock);retired=true;for(auto& item:sockets)all.push_back(item.second);}
    for(auto& p:all)stop(p);
    {Guard lock(sockets_lock);sockets.clear();notifications.clear();modes.clear();udp_state.reset();}
}
inline void resume(){
    traffic_enabled=true;std::vector<SOCKET> notify;
    {Guard lock(sockets_lock);for(const auto& item:notifications)notify.push_back(item.first);for(const auto& item:sockets)item.second->queue_changed.notify_all();}
    for(SOCKET s:notify){
        notify_socket(s,FD_WRITE);auto p=find_state(s);
        fd_set read;FD_ZERO(&read);FD_SET(s,&read);timeval zero{};
        if((p&&p->readable_bytes)||raw_select(0,&read,nullptr,nullptr,&zero)>0)notify_socket(s,FD_READ);
    }
}
}
