#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <algorithm>
// Diagnostic facts only. No payload, credential, address or caller-controlled
// label is exported, and malformed observations never affect transport policy.
namespace kkobserve {
enum Api {Send,Recv,RecvFrom,RecvFromTcp,Fionread,Peek,DecryptFailure,Malformed};
constexpr std::array<uint32_t,12> ids{{0,1131,1020,1120,7080,7070,1151,3320,1201,2010,2030,2250}};
static std::array<std::atomic<uint32_t>,8> api{};
static std::array<std::atomic<uint32_t>,12> decoded{},delivered{};
struct Stream {
    std::array<unsigned char,16> head{};size_t have=0,remaining=0;int index=-1;bool disabled=false;
    void feed(const void* data,size_t length,std::array<std::atomic<uint32_t>,12>& counts){
        const auto* p=static_cast<const unsigned char*>(data);
        while(length&&!disabled){
            if(remaining){size_t n=(std::min)(length,remaining);p+=n;length-=n;remaining-=n;if(!remaining){if(index>=0)++counts[static_cast<size_t>(index)];have=0;}continue;}
            size_t n=(std::min)(length,head.size()-have);std::memcpy(head.data()+have,p,n);have+=n;p+=n;length-=n;if(have<head.size())continue;
            uint16_t magic=0,mask=0;uint32_t size=0;std::memcpy(&magic,head.data(),2);std::memcpy(&mask,head.data()+2,2);std::memcpy(&size,head.data()+4,4);
            if(magic!=0xaaee||size<16||size%8||size>1024*1024-8||mask!=((size^0xbbcc)&0x88aa)){disabled=true;++api[Malformed];return;}
            uint64_t encoded=0,key=0;std::memcpy(&encoded,head.data()+8,8);std::memcpy(&key,"00Na~1fd",8);encoded^=key;
            uint32_t id=static_cast<uint32_t>((encoded>>3)|(encoded<<61));index=-1;
            for(size_t i=0;i<ids.size();i++)if(ids[i]==id){index=static_cast<int>(i);break;}
            remaining=size-8;head.fill(0);
        }
    }
};
struct Snapshot {uint32_t size=136,version=1;uint32_t api[8]{},decoded[12]{},delivered[12]{};};
static_assert(sizeof(Snapshot)==136,"metadata-only observation ABI");
inline Snapshot snapshot(){Snapshot out;for(size_t i=0;i<8;i++)out.api[i]=api[i].load();for(size_t i=0;i<12;i++){out.decoded[i]=decoded[i].load();out.delivered[i]=delivered[i].load();}return out;}
}
