#pragma once
static void hook_transaction_contract(){
    using namespace kkhooks;
    void* a=reinterpret_cast<void*>(1);void* b=reinterpret_cast<void*>(2);void* c=reinterpret_cast<void*>(3);
    void** slots[]={&a,&b,&c};
    auto reset=[&]{committed.clear();rollback_error=0;a=reinterpret_cast<void*>(1);b=reinterpret_cast<void*>(2);c=reinterpret_cast<void*>(3);};
    auto plan=[&]{std::vector<Patch> result;for(size_t i=0;i<3;i++)assert(add(result,slots[i],reinterpret_cast<void*>(i+1),reinterpret_cast<void*>(i+101)));return result;};
    for(int fail=0;fail<3;fail++){
        reset();auto changes=plan();int calls=0;
        auto exchange=[&](void** target,void* old,void* next){if(calls++==fail)return false;if(*target!=old)return false;*target=next;return true;};
        assert(!commit(changes,exchange));for(size_t i=0;i<3;i++)assert(*slots[i]==reinterpret_cast<void*>(i+1));assert(committed.empty()&&!rollback_error);
    }
    reset();auto changes=plan();int calls=0;
    assert(!commit(changes,[&](void** target,void* old,void* next){++calls;if(calls==3||calls==5)return false;if(*target!=old)return false;*target=next;return true;}));assert(rollback_error==1);
    reset();changes=plan();
    assert(!commit(changes,[&](void** target,void* old,void* next){if(target==&b){a=reinterpret_cast<void*>(999);return false;}if(*target!=old)return false;*target=next;return true;}));assert(a==reinterpret_cast<void*>(999)&&rollback_error==1);
    reset();changes=plan();auto exchange=[](void** target,void* old,void* next){if(*target!=old)return false;*target=next;return true;};
    assert(commit(changes,exchange));assert(committed.size()==3);auto empty=plan();assert(empty.empty()&&commit(empty,exchange)&&committed.size()==3);
    phase=Ready;verified=epoch.load();assert(ready());++epoch;assert(!ready());verified=epoch.load();assert(ready());
    a=reinterpret_cast<void*>(999);assert(!validate());
    reset();phase=Cold;verified=0;
    // Concurrent callers use the same production lifecycle lock.
    std::thread first([&]{std::lock_guard<std::mutex> lock(lifecycle);auto p=plan();assert(commit(p,exchange));});
    std::thread second([&]{std::lock_guard<std::mutex> lock(lifecycle);auto p=plan();assert(commit(p,exchange));});first.join();second.join();assert(committed.size()==3);
    reset();
    // Bounded malformed PE: no out-of-range dereference or partial patch.
    alignas(4) std::array<unsigned char,4096> image{};std::vector<Patch> no_changes;
    assert(!kknet::collect_module(reinterpret_cast<HMODULE>(image.data()),image.size(),no_changes)&&no_changes.empty());
    IMAGE_DOS_HEADER dos{};dos.e_magic=IMAGE_DOS_SIGNATURE;dos.e_lfanew=128;std::memcpy(image.data(),&dos,sizeof(dos));
    IMAGE_NT_HEADERS32 nt{};nt.Signature=IMAGE_NT_SIGNATURE;nt.FileHeader.Machine=IMAGE_FILE_MACHINE_I386;nt.FileHeader.SizeOfOptionalHeader=sizeof(IMAGE_OPTIONAL_HEADER32);nt.OptionalHeader.Magic=IMAGE_NT_OPTIONAL_HDR32_MAGIC;nt.OptionalHeader.NumberOfRvaAndSizes=16;
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT]={512,40};std::memcpy(image.data()+128,&nt,sizeof(nt));
    IMAGE_IMPORT_DESCRIPTOR imported{};imported.Name=600;imported.FirstThunk=768;imported.OriginalFirstThunk=1024;std::memcpy(image.data()+512,&imported,sizeof(imported));
    std::memcpy(image.data()+600,"WS2_32.dll",11);IMAGE_THUNK_DATA32 thunk{};thunk.u1.AddressOfData=1200;std::memcpy(image.data()+1024,&thunk,sizeof(thunk));std::memcpy(image.data()+1202,"send",5);
    void* send=reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"WS2_32.dll"),"send"));std::memcpy(image.data()+768,&send,sizeof(send));
    assert(kknet::collect_module(reinterpret_cast<HMODULE>(image.data()),image.size(),no_changes)&&no_changes.size()==1);
    void* recv=reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"WS2_32.dll"),"recv"));std::memcpy(image.data()+768,&recv,sizeof(recv));no_changes.clear();
    assert(!kknet::collect_module(reinterpret_cast<HMODULE>(image.data()),image.size(),no_changes)&&no_changes.empty());
    dos.e_lfanew=0x7fffffff;std::memcpy(image.data(),&dos,sizeof(dos));
    assert(!kknet::collect_module(reinterpret_cast<HMODULE>(image.data()),image.size(),no_changes)&&no_changes.empty());
    puts("PASS: preflight; each-slot failure; rollback failure; foreign-slot preservation; idempotence; epoch invalidation; serialized concurrent install; bounded PE.");
}
