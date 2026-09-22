// Own encrypted-record format, shared contract with native_crypto.py.
// Windows CNG performs HMAC-SHA256 and AES-256-GCM; no bespoke cipher.
#pragma once
#include <vector>
#include <algorithm>
#include <stdexcept>
namespace kkrecord {
constexpr size_t header_size=52,max_plain=32768;
constexpr uint64_t limit=uint64_t(1)<<32;
inline void be(unsigned char* out,uint64_t n,size_t count){for(size_t i=0;i<count;i++){out[count-1-i]=static_cast<unsigned char>(n);n>>=8;}}
inline uint64_t number(const unsigned char* p,size_t n){uint64_t v=0;for(size_t i=0;i<n;i++)v=(v<<8)|p[i];return v;}
inline bool header(const unsigned char* p,size_t n){return n>=header_size&&!std::memcmp(p,"KKE1",4)&&p[4]>=1&&p[4]<=3&&p[5]<=1&&!p[6]&&!p[7]&&number(p+40,8)<limit&&number(p+48,4)>0&&number(p+48,4)<=max_plain;}
inline bool derive(const unsigned char master[32],const unsigned char sid[16],const unsigned char cid[16],unsigned char channel,unsigned char direction,unsigned char out[32]){
    static const unsigned char domain[]="KK-native-record-v1";
    BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;bool ok=false;
    unsigned char context[sizeof(domain)+34];std::memcpy(context,domain,sizeof(domain));context[sizeof(domain)]=channel;context[sizeof(domain)+1]=direction;
    std::memcpy(context+sizeof(domain)+2,sid,16);std::memcpy(context+sizeof(domain)+18,cid,16);
    if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,BCRYPT_ALG_HANDLE_HMAC_FLAG)>=0&&BCryptCreateHash(alg,&hash,nullptr,0,const_cast<PUCHAR>(master),32,0)>=0)
        ok=BCryptHashData(hash,context,sizeof(context),0)>=0&&BCryptFinishHash(hash,out,32,0)>=0;
    if(hash)BCryptDestroyHash(hash);if(alg)BCryptCloseAlgorithmProvider(alg,0);return ok;
}
inline bool aes(bool encrypt,const unsigned char key[32],const unsigned char* head,const unsigned char* input,size_t n,unsigned char* output,unsigned char tag[16]){
    BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_KEY_HANDLE handle=nullptr;bool ok=false;ULONG got=0;
    unsigned char nonce[12]{};std::memcpy(nonce+4,head+40,8);
    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO info;BCRYPT_INIT_AUTH_MODE_INFO(info);
    info.pbNonce=nonce;info.cbNonce=12;info.pbAuthData=const_cast<PUCHAR>(head);info.cbAuthData=header_size;info.pbTag=tag;info.cbTag=16;
    if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_AES_ALGORITHM,nullptr,0)>=0&&BCryptSetProperty(alg,BCRYPT_CHAINING_MODE,reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_GCM)),sizeof(BCRYPT_CHAIN_MODE_GCM),0)>=0&&BCryptGenerateSymmetricKey(alg,&handle,nullptr,0,const_cast<PUCHAR>(key),32,0)>=0){
        auto call=encrypt?BCryptEncrypt:BCryptDecrypt;
        ok=call(handle,const_cast<PUCHAR>(input),static_cast<ULONG>(n),&info,nullptr,0,output,static_cast<ULONG>(n),&got,0)>=0&&got==n;
    }
    if(handle)BCryptDestroyKey(handle);if(alg)BCryptCloseAlgorithmProvider(alg,0);return ok;
}
struct Records {
    unsigned char keys[2][32]{},sid[16]{},cid[16]{},channel=0,tx=0;
    uint64_t sent=0,next=0,high=0;bool have_high=false,initialized=false;
    std::array<uint64_t,16> seen{};
    ~Records(){SecureZeroMemory(keys,sizeof(keys));}
    bool init(const unsigned char master[32],const unsigned char id[16],const unsigned char connection[16],unsigned char ch,bool server){
        if(initialized)return false;std::memcpy(sid,id,16);std::memcpy(cid,connection,16);channel=ch;tx=server?1:0;
        initialized=derive(master,sid,cid,ch,0,keys[0])&&derive(master,sid,cid,ch,1,keys[1]);return initialized;
    }
    bool seal(const unsigned char* data,size_t n,std::vector<unsigned char>& out){
        if(!initialized||!n||n>max_plain||sent>=limit)return false;
        out.resize(header_size+n+16);auto h=out.data();std::memcpy(h,"KKE1",4);h[4]=channel;h[5]=tx;h[6]=h[7]=0;
        std::memcpy(h+8,sid,16);std::memcpy(h+24,cid,16);be(h+40,sent++,8);be(h+48,n,4);
        if(!aes(true,keys[tx],h,data,n,h+header_size,h+header_size+n)){out.clear();return false;}return true;
    }
    bool open(const unsigned char* p,size_t n,std::vector<unsigned char>& out){
        if(!initialized||!header(p,n)||p[4]!=channel||p[5]!=1-tx||std::memcmp(p+8,sid,16)||std::memcmp(p+24,cid,16))return false;
        uint64_t seq=number(p+40,8);size_t count=static_cast<size_t>(number(p+48,4));if(n!=header_size+count+16)return false;
        if(channel==3){if(have_high&&seq<=high){uint64_t age=high-seq;if(age>=1024||(seen[static_cast<size_t>(age/64)]>>(age%64)&1))return false;}}
        else if(seq!=next)return false;
        unsigned char tag[16];std::memcpy(tag,p+header_size+count,16);out.resize(count);
        if(!aes(false,keys[1-tx],p,p+header_size,count,out.data(),tag)){SecureZeroMemory(out.data(),out.size());out.clear();return false;}
        if(channel==3){
            if(!have_high||seq>high){
                uint64_t delta=have_high?seq-high:1024;auto old=seen;seen.fill(0);
                if(delta<1024)for(size_t i=0;i<1024-static_cast<size_t>(delta);i++)if(old[i/64]>>(i%64)&1)seen[(i+static_cast<size_t>(delta))/64]|=uint64_t(1)<<((i+delta)%64);
                high=seq;have_high=true;
            }
            uint64_t age=high-seq;seen[static_cast<size_t>(age/64)]|=uint64_t(1)<<(age%64);
        }else ++next;return true;
    }
};
}
