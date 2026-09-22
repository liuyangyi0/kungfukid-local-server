#pragma once
#include <vector>
#include <atomic>
#include <mutex>
namespace kkhooks {
enum Phase:DWORD {Cold=0,Preparing=1,Ready=2,Failed=3,Retired=4};
struct Patch {void** slot;void* original;void* replacement;};
static std::mutex lifecycle;
static std::atomic<DWORD> phase{Cold},epoch{1},verified{0},unsupported{0},last_error{0},rollback_error{0};
static std::vector<Patch> committed;
inline bool ready(){return phase==Ready&&verified==epoch;}
inline bool read(const void* address,void* output,size_t size){SIZE_T got=0;return ReadProcessMemory(GetCurrentProcess(),address,output,size,&got)&&got==size;}
inline bool current(const Patch& p,void* expected){void* got=nullptr;return read(p.slot,&got,sizeof(got))&&got==expected;}
inline bool add(std::vector<Patch>& plan,void** slot,void* original,void* replacement){
    for(const auto& p:committed)if(p.slot==slot)return p.replacement==replacement&&current(p,replacement);
    for(const auto& p:plan)if(p.slot==slot)return p.original==original&&p.replacement==replacement;
    if(reinterpret_cast<uintptr_t>(slot)%sizeof(void*)||!current({slot,original,replacement},original))return false;
    plan.push_back({slot,original,replacement});return true;
}
inline bool validate(){for(const auto& p:committed)if(!current(p,p.replacement))return false;return true;}
template<class Exchange> bool commit(std::vector<Patch>& plan,Exchange exchange){
    if(!validate())return false;
    for(const auto& p:plan)if(!current(p,p.original))return false;
    // Allocate before changing any slot: a vector allocation failure cannot
    // strand an untracked patch in live code.
    committed.reserve(committed.size()+plan.size());size_t count=0;
    for(;count<plan.size();count++)if(!exchange(plan[count].slot,plan[count].original,plan[count].replacement))break;
    bool ok=count==plan.size();if(ok)for(const auto& p:plan)if(!current(p,p.replacement)){ok=false;break;}
    if(!ok){
        // Include the failed slot if its write succeeded but protection failed.
        size_t end=(std::min)(count+1,plan.size());
        for(size_t i=end;i>0;i--){auto& p=plan[i-1];
            if(current(p,p.replacement)){if(!exchange(p.slot,p.replacement,p.original))rollback_error=1;}
            else if(!current(p,p.original))rollback_error=1; // never overwrite a foreign owner
        }
        return false;
    }
    committed.insert(committed.end(),plan.begin(),plan.end());return true;
}
// Read-only bounded image reader. Uses OS-reported image extent, not unchecked
// RVA arithmetic or direct casts through malformed/partly committed pages.
struct Image {
    unsigned char* base;size_t size;
    bool range(size_t at,size_t n)const{return at<=size&&n<=size-at;}
    template<class T>bool get(size_t at,T& v)const{return range(at,sizeof(v))&&read(base+at,&v,sizeof(v));}
    bool text(size_t at,char* out,size_t cap)const{
        for(size_t i=0;i<cap;i++){if(!range(at+i,1)||!read(base+at+i,out+i,1))return false;if(!out[i])return true;}return false;
    }
};
}
