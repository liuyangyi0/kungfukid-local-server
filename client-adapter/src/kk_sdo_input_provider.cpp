// User-authorized input-provider change in the isolated SDK only. Does not
// read text/passwords, dismiss warnings, synthesize input, or report login OK.
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cwchar>
#pragma comment(lib,"user32.lib")
using SetMode=void(__thiscall*)(void*,bool);
static HWND target;
static HWND candidates[8];
static unsigned count;
static WNDPROC previous;
static UINT change_message;
static LONG called;
static HMODULE dui;
using DoEvent=void(__thiscall*)(void*,void*);
static DoEvent original_event;
static SetMode set_strong;
static SetMode set_mask;
static BYTE* password_control;
static BOOL CALLBACK top(HWND,LPARAM);
static void report(const char* stage,int strong=-1,int masked=-1,int requested=-1);
static bool qualifies(void* self){
    // A return from the game can rebuild CLoginDlg/CSdoEditUI. Do not keep
    // authorizing a stale pointer captured during the first login screen.
    for(unsigned pass=0;pass<2;++pass){
        if(IsWindow(target)){
            DWORD pid=0;GetWindowThreadProcessId(target,&pid);
            BYTE* owner=(BYTE*)GetWindowLongPtrW(target,GWLP_USERDATA);
            if(pid==GetCurrentProcessId()&&owner&&*(HWND*)(owner+4)==target){
                BYTE* edit=*(BYTE**)(owner+876);
                void* expected=(void*)GetProcAddress(dui,"??_7CSdoEditUI@DuiLib@@6B@");
                if(edit==self&&expected&&*(void**)edit==expected){
                    if(password_control!=edit){password_control=edit;report("password_control_rebound");}
                    return true;
                }
            }
        }
        if(pass==0){count=0;EnumWindows(top,0);if(count!=1)return false;target=candidates[0];}
    }
    return false;
}
static bool normalize(BYTE* edit){
    set_mask(edit,true);set_strong(edit,false);
    BYTE* c=*(BYTE**)(edit+1276);
    bool ready=c&&*(DWORD*)(c+60)==1&&*(DWORD*)(c+92)==1&&*(DWORD*)(c+188)==1&&
        *(void**)(c+104)&&*(void**)(c+108)&&*(void**)(c+112)&&*(void**)(c+116);
    if(ready&&*(DWORD*)(c+64)==1){*(DWORD*)(c+64)=0;return true;}return false;
}
static void __fastcall user_event(void* self,void*,void* event){
    // The event is opaque: never inspect characters, key codes or text. Only
    // normalize the authorized password control around its single native call.
    __try{if(qualifies(self))normalize((BYTE*)self);}__except(EXCEPTION_EXECUTE_HANDLER){}
    original_event(self,event);
    __try{if(qualifies(self))normalize((BYTE*)self);}__except(EXCEPTION_EXECUTE_HANDLER){}
}
static void report(const char* stage,int strong,int masked,int requested){
    FILE* f=nullptr;fopen_s(&f,"C:\\KK-Lab\\sdo-original-window-20260913-1931\\input-provider.log","ab");
    if(f){fprintf(f,"pid=%lu thread=%lu stage=%s strong=%d masked=%d requested_provider=%d\n",GetCurrentProcessId(),GetCurrentThreadId(),stage,strong,masked,requested);fclose(f);}
}
static bool apply(HWND w){
    __try{
        BYTE* owner=(BYTE*)GetWindowLongPtrW(w,GWLP_USERDATA);
        if(!owner || *(HWND*)(owner+4)!=w){report("window_object_rejected");return false;}
        // CLoginDlg 4AEA50/4AE270 use +876 for CSdoEditUI; validate its actual
        // vtable before reading mode fields or calling the native exported API.
        BYTE* edit=*(BYTE**)(owner+876);
        void* expected=(void*)GetProcAddress(dui,"??_7CSdoEditUI@DuiLib@@6B@");
        if(!edit||!expected||*(void**)edit!=expected){report("password_control_rejected");return false;}
        auto strong=(SetMode)GetProcAddress(dui,"?SetStrongPasswordMode@CSdoEditUI@DuiLib@@QAEX_N@Z");
        auto masked=(SetMode)GetProcAddress(dui,"?SetPasswordMode@CSdoEditUI@DuiLib@@QAEX_N@Z");
        if(!strong||!masked){report("mode_exports_missing");return false;}
        set_strong=strong;set_mask=masked;
        report("before",edit[1128],edit[1285]);
        masked(edit,true);strong(edit,false);
        BYTE* control=*(BYTE**)(edit+1276);
        int requested=control?*(int*)(control+188):-1;
        if(control){
            FILE* f=nullptr;fopen_s(&f,"C:\\KK-Lab\\sdo-original-window-20260913-1931\\input-provider.log","ab");
            if(f){fprintf(f,"pid=%lu stage=controller active_provider=%lu store_present=%d algorithm_present=%d byte_present=%d index_present=%d\n",GetCurrentProcessId(),*(DWORD*)(control+92),*(void**)(control+104)!=nullptr,*(void**)(control+108)!=nullptr,*(void**)(control+112)!=nullptr,*(void**)(control+116)!=nullptr);fclose(f);}
        }
        bool ready=control&&*(DWORD*)(control+60)==1&&*(DWORD*)(control+92)==1&&requested==1&&
            *(void**)(control+104)&&*(void**)(control+108)&&*(void**)(control+112)&&*(void**)(control+116);
        if(ready){
            // 1004BF80 latches +64=1 on failed provider startup but changes
            // requested provider to 1; later successful user-mode Init does not
            // clear it. 1004E140 suppresses characters while this latch remains.
            // Restore the constructor's clear latch only after user-mode Init
            // and object presence have been verified. Leave read-only+200 alone.
            DWORD before=*(DWORD*)(control+64);
            if(before==1){*(DWORD*)(control+64)=0;report("cleared_failed_provider_input_latch");}
            else report(before==0?"input_latch_already_clear":"unexpected_input_latch");
        }else report("user_mode_not_fully_initialized");
        bool ok=ready&&edit[1128]==0&&edit[1285]==1&&*(DWORD*)(control+64)==0;
        if(edit[1128]==0&&edit[1285]==1&&!original_event){
            auto event=(DoEvent)GetProcAddress(dui,"?DoEvent@CSdoEditUI@DuiLib@@UAEXAAUtagTEventUI@2@@Z");
            auto slot=(uintptr_t*)expected+68; // original DoEvent vslot +272
            if(!event||*slot!=(uintptr_t)event){report("event_slot_rejected");return false;}
            DWORD protect;if(!VirtualProtect(slot,4,PAGE_READWRITE,&protect)){report("event_slot_protection_failed");return false;}
            password_control=edit;original_event=event;*slot=(uintptr_t)user_event;
            DWORD ignored;VirtualProtect(slot,4,protect,&ignored);report("user_mode_event_adapter_ready");
        }
        report(ok?"user_mode_selected":"user_mode_pending_native_init",edit[1128],edit[1285],requested);
        return original_event!=nullptr;
    }__except(EXCEPTION_EXECUTE_HANDLER){report("mode_exception");return false;}
}
static LRESULT CALLBACK proc(HWND w,UINT msg,WPARAM wp,LPARAM lp){
    if(msg==change_message && InterlockedCompareExchange(&called,1,0)==0){
        apply(w);
        if((WNDPROC)GetWindowLongPtrW(w,GWLP_WNDPROC)==proc)SetWindowLongPtrW(w,GWLP_WNDPROC,(LONG_PTR)previous);
        return 0;
    }
    return CallWindowProcW(previous,w,msg,wp,lp);
}
static BOOL CALLBACK visit(HWND w,LPARAM){
    DWORD pid=0;GetWindowThreadProcessId(w,&pid);if(pid!=GetCurrentProcessId())return TRUE;
    wchar_t cls[128];GetClassNameW(w,cls,128);
    if(wcscmp(cls,L"igwUserPwdLoginDialog"))return TRUE;
    for(unsigned i=0;i<count;++i)if(candidates[i]==w)return TRUE;
    if(count<8)candidates[count++]=w;return TRUE;
}
static BOOL CALLBACK top(HWND w,LPARAM){visit(w,0);EnumChildWindows(w,visit,0);return TRUE;}
static DWORD WINAPI worker(void*){
    wchar_t image[32768];if(!GetModuleFileNameW(nullptr,image,32768))return 1;
    if(_wcsicmp(image,L"C:\\KK-Lab\\sdo-native-client-20260913-1931\\sdo\\sdologin\\sdologin.exe")){report("image_rejected");return 2;}
    dui=GetModuleHandleW(L"duilib.dll");if(!dui){report("duilib_missing");return 3;}
    for(unsigned attempt=0;attempt<900;++attempt){
        count=0;EnumWindows(top,0);if(count==1)break;Sleep(100);
    }
    if(count!=1){report(count?"password_window_not_unique":"password_window_not_created");return 4;}
    target=candidates[0];change_message=RegisterWindowMessageW(L"KK.Local.Sdo.UserMode.Provider.V1");
    previous=(WNDPROC)GetWindowLongPtrW(target,GWLP_WNDPROC);
    if(!previous){report("window_proc_missing");return 5;}
    if(!SetWindowLongPtrW(target,GWLP_WNDPROC,(LONG_PTR)proc)){report("subclass_failed");return 6;}
    DWORD_PTR result=0;
    if(!SendMessageTimeoutW(target,change_message,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,5000,&result)){
        if((WNDPROC)GetWindowLongPtrW(target,GWLP_WNDPROC)==proc)SetWindowLongPtrW(target,GWLP_WNDPROC,(LONG_PTR)previous);
        report("dispatch_failed");return 7;
    }
    report("ui_dispatch_returned");return 0;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){
    if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(h);HANDLE t=CreateThread(nullptr,0,worker,nullptr,0,nullptr);if(t)CloseHandle(t);}return TRUE;
}
