// Self-owned loopback pair only. Never loads an original SDK or client.
#pragma once
static std::atomic<bool> queue_stall{true};
static int WSAAPI queue_test_send(SOCKET s,const char* data,int n,int flags){
    if(queue_stall){WSASetLastError(WSAEWOULDBLOCK);return SOCKET_ERROR;}return ::send(s,data,n,flags);
}
static void queue_contract(){
    SOCKET listener=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);assert(listener!=INVALID_SOCKET);
    sockaddr_in endpoint{};endpoint.sin_family=AF_INET;endpoint.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    assert(bind(listener,reinterpret_cast<sockaddr*>(&endpoint),sizeof(endpoint))==0);assert(listen(listener,2)==0);
    int length=sizeof(endpoint);assert(getsockname(listener,reinterpret_cast<sockaddr*>(&endpoint),&length)==0);
    {AcquireSRWLockExclusive(&state_lock);ticket.game_port=ntohs(endpoint.sin_port);ReleaseSRWLockExclusive(&state_lock);}
    SOCKET s=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);assert(connect(s,reinterpret_cast<sockaddr*>(&endpoint),sizeof(endpoint))==0);
    SOCKET peer=accept(listener,nullptr,nullptr);assert(peer!=INVALID_SOCKET);
    u_long mode=1;assert(kknet::ioctls_hook(s,FIONBIO,&mode)==0);auto saved=kknet::raw_send;kknet::raw_send=&queue_test_send;
    std::vector<char> input(kknet::queue_limit,'q');
    assert(kknet::send_hook(s,input.data(),static_cast<int>(input.size()),0)==static_cast<int>(input.size()));
    auto state=kknet::find_state(s);assert(state&&state->queued==kknet::queue_limit);
    input.assign(input.size(),'z');assert(kknet::send_hook(s,"different",9,0)==SOCKET_ERROR&&WSAGetLastError()==WSAEWOULDBLOCK);
    assert(!state->dead);assert(state->records.sent==8);
    DWORD timeout=25;assert(setsockopt(s,SOL_SOCKET,SO_SNDTIMEO,reinterpret_cast<char*>(&timeout),sizeof(timeout))==0);
    mode=0;assert(kknet::ioctls_hook(s,FIONBIO,&mode)==0);ULONGLONG at=GetTickCount64();
    assert(kknet::send_hook(s,"wait",4,0)==SOCKET_ERROR&&WSAGetLastError()==WSAETIMEDOUT);assert(GetTickCount64()-at<1000);
    fd_set write;FD_ZERO(&write);FD_SET(s,&write);timeval zero{};assert(kknet::select_hook(0,nullptr,&write,nullptr,&zero)==0);
    // Readiness must not take the receive lock even if a reader owns it.
    AcquireSRWLockExclusive(&state->rx);state->readable_bytes=1;
    fd_set read;FD_ZERO(&read);FD_SET(s,&read);assert(kknet::select_hook(0,&read,nullptr,nullptr,&zero)==1);
    state->readable_bytes=0;ReleaseSRWLockExclusive(&state->rx);
    assert(kknet::close_hook(s)==0);assert(!state->worker.joinable()&&state->queued==0&&!kknet::find_state(s));::closesocket(peer);
    // A new handle association must have a new connection nonce and no backlog.
    s=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);assert(connect(s,reinterpret_cast<sockaddr*>(&endpoint),sizeof(endpoint))==0);peer=accept(listener,nullptr,nullptr);
    mode=1;assert(kknet::ioctls_hook(s,FIONBIO,&mode)==0);
    char a[]="abc",b[]="defg";WSABUF bufs[2]={{3,a},{4,b}};DWORD sent=0;
    assert(kknet::send_buffers(s,bufs,2,&sent,0,nullptr,nullptr)==0&&sent==7);
    auto next=kknet::find_state(s);assert(next&&next->queued==7&&std::memcmp(next->records.cid,state->records.cid,16));
    WSAOVERLAPPED overlapped{};assert(kknet::send_buffers(s,bufs,2,&sent,0,&overlapped,nullptr)==SOCKET_ERROR&&WSAGetLastError()==WSAEOPNOTSUPP);
    std::thread closer([&]{assert(kknet::close_hook(s)==0);});closer.join();assert(!next->worker.joinable());
    ::closesocket(peer);::closesocket(listener);kknet::raw_send=saved;
    puts("PASS: owned queue; changed retry; full/nonblocking; blocking timeout; no readiness lock wait; scatter send; close/reuse; worker join.");
}
