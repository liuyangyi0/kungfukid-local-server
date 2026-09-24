#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <wchar.h>
#if !defined(_M_IX86) && !defined(__i386__)
#error This initializer requires an i686 build.
#endif
#ifdef KK_OFFICIAL_FLOW_PORTABLE
#ifndef KK_OFFICIAL_FLOW_MANUAL_START
#error Portable releases must be explicitly started by the authenticated launcher.
#endif
static wchar_t log_path[MAX_PATH];
static wchar_t ready_path[MAX_PATH];
static int init_paths(HINSTANCE module){
 DWORD length=GetModuleFileNameW(module,log_path,MAX_PATH);
 wchar_t* slash;
 FILE* file;
 if(!length||length>=MAX_PATH)return 0;
 slash=wcsrchr(log_path,L'\\');
 if(!slash||(size_t)(slash-log_path)+1+wcslen(L"kk1-roleprop-ready.txt")>=MAX_PATH)return 0;
 slash[1]=0;
 wcscpy(ready_path,log_path);
 wcscat(log_path,L"kk1-official-flow.log");
 wcscat(ready_path,L"kk1-roleprop-ready.txt");
 // Readiness depends on this log. Fail before registering callbacks or starting
 // the worker instead of allowing a hidden startup failure and launcher timeout.
 file=_wfopen(log_path,L"ab");
 if(!file)return 0;
 if(fclose(file)!=0)return 0;
 return 1;
}
#else
static const wchar_t* log_path=L"C:\\KK-Lab\\kk1-official-flow.log";
static const wchar_t* ready_path=L"C:\\KK-Lab\\kk1-roleprop-ready.txt";
#endif
static CRITICAL_SECTION g_lock;
static int g_lock_ready;
static PVOID g_veh;
static WNDPROC prior;
static HWND target;
static UINT message;
static LONG invoked;
static LONG dumped;
static UINT_PTR timer;
static UINT_PTR delay_timer;
static LONG ready_written;
static LONG selector_started;
static LONG bridge_ready;
static void log_line(const char* fmt,...){
 if(g_lock_ready)EnterCriticalSection(&g_lock);
 FILE* f=_wfopen(log_path,L"ab");
 if(f){
  SYSTEMTIME st;GetLocalTime(&st);
  fprintf(f,"%04u-%02u-%02uT%02u:%02u:%02u.%03u ",st.wYear,st.wMonth,st.wDay,st.wHour,st.wMinute,st.wSecond,st.wMilliseconds);
  va_list ap;va_start(ap,fmt);vfprintf(f,fmt,ap);va_end(ap);
  fprintf(f,"\r\n");fflush(f);fclose(f);
 }
 if(g_lock_ready)LeaveCriticalSection(&g_lock);
}
static int read_u32(uint32_t addr,uint32_t* out){
 SIZE_T got=0;
 return ReadProcessMemory(GetCurrentProcess(),(LPCVOID)(uintptr_t)addr,out,4,&got)&&got==4;
}
static int has_prologue(uint32_t addr){
 BYTE value=0;SIZE_T got=0;
 return ReadProcessMemory(GetCurrentProcess(),(LPCVOID)(uintptr_t)addr,&value,1,&got)&&got==1&&value==0x55;
}
static void write_ready(uint32_t table){
 HANDLE f;DWORD n;char b[80];int len;
 if(InterlockedCompareExchange(&ready_written,1,0)!=0)return;
 len=_snprintf(b,sizeof(b),"0x%08lX\r\n",(unsigned long)table);
 f=CreateFileW(ready_path,GENERIC_WRITE,FILE_SHARE_READ,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
 if(f!=INVALID_HANDLE_VALUE){WriteFile(f,b,(DWORD)len,&n,NULL);CloseHandle(f);}
 log_line("roleprop_ready table=0x%08lX",(unsigned long)table);
}
static void enter_selector_3(void){
 void* get_app=(void*)(uintptr_t)0x00990070;
 void* set_sel=(void*)(uintptr_t)0x0098FDE0;
 if(!has_prologue(0x00990070)||!has_prologue(0x0098FDE0)){log_line("selector_identity_rejected");return;}
 log_line("enter_selector_3");
#ifdef _MSC_VER
 __asm {
  push 3
  call get_app
  mov ecx,eax
  call set_sel
 }
#else
 __asm__ volatile(
  "push $3\n\t"
  "call *%[get]\n\t"
  "mov %%eax, %%ecx\n\t"
  "call *%[set]\n\t"
  :
  : [get] "r"(get_app), [set] "r"(set_sel)
  : "eax","ecx","edx","memory"
 );
#endif
}
static LONG CALLBACK veh(PEXCEPTION_POINTERS ep){
 DWORD code;CONTEXT* c;uint32_t eip,i,frame,ret,av;
 if(!ep||!ep->ExceptionRecord||!ep->ContextRecord)return EXCEPTION_CONTINUE_SEARCH;
 code=ep->ExceptionRecord->ExceptionCode;
 if(code==0x40010006UL||code==0x406D1388UL)return EXCEPTION_CONTINUE_SEARCH;
 if(InterlockedCompareExchange(&dumped,1,0)!=0)return EXCEPTION_CONTINUE_SEARCH;
 c=ep->ContextRecord;eip=(uint32_t)c->Eip;
 log_line("FATAL code=0x%08lX eip=0x%08lX eax=0x%08lX ecx=0x%08lX",(unsigned long)code,(unsigned long)eip,(unsigned long)c->Eax,(unsigned long)c->Ecx);
 if(ep->ExceptionRecord->NumberParameters>=2){
  av=(uint32_t)ep->ExceptionRecord->ExceptionInformation[1];
  log_line("av op=%lu av_addr=0x%08lX",(unsigned long)ep->ExceptionRecord->ExceptionInformation[0],(unsigned long)av);
 }
 frame=(uint32_t)c->Ebp;
 for(i=0;i<12;i++){
  uint32_t next=0;
  if(frame<0x10000||!read_u32(frame,&next)||!read_u32(frame+4,&ret))break;
  log_line("frame[%u] ret=0x%08lX",i,(unsigned long)ret);
  if(next<=frame)break;frame=next;
 }
 return EXCEPTION_CONTINUE_SEARCH;
}
static void poll_roleprop(HWND w){
 uint32_t table=0;
 read_u32(0x017C86F0,&table);
 if(table){write_ready(table);if(timer){KillTimer(w,0x4C3);timer=0;}return;}
 log_line("roleprop_still_null");
}
static LRESULT CALLBACK window_proc(HWND w,UINT m,WPARAM a,LPARAM b){
#ifdef KK_OFFICIAL_FLOW_MANUAL_START
 if(m==message){
  uint32_t table=0,router=0,selector=0;
  read_u32(0x017C86F0,&table);read_u32(0x017C89AC,&router);
  if(router)read_u32(router,&selector);
  if(!table||selector!=4){log_line("manual_start_rejected selector=%lu resources=%d",(unsigned long)selector,table!=0);return 0;}
 }
#endif
 if(m==message && InterlockedCompareExchange(&invoked,1,0)==0){
  typedef int (__stdcall *Callback)(int,void*,int,void*);
  struct {DWORD unused;const char* session;} args={0,"kk-local-lobby-session-a07"};
  uint32_t table=0;
  log_line("sdo_enter");
  log_line("sdo_return %d",((Callback)(uintptr_t)0x00912C50)(0,&args,0,NULL));
  read_u32(0x017C86F0,&table);
  log_line("roleprop_after_sdo 0x%08lX",(unsigned long)table);
  if(table)write_ready(table);
  else{
   delay_timer=SetTimer(w,0x4C4,12000,NULL);
   log_line("selector3_deferred_ms 12000");
  }
  timer=SetTimer(w,0x4C3,250,NULL);
  return 0;
 }
 if(m==WM_TIMER && a==0x4C4){
  KillTimer(w,0x4C4);delay_timer=0;
  if(InterlockedCompareExchange(&selector_started,1,0)==0){
   uint32_t table=0;read_u32(0x017C86F0,&table);
   if(!table)enter_selector_3();
   else write_ready(table);
  }
  return 0;
 }
 if(m==WM_TIMER && a==0x4C3){poll_roleprop(w);return 0;}
 return CallWindowProcA(prior,w,m,a,b);
}
static BOOL CALLBACK enum_window(HWND w,LPARAM unused){
 DWORD pid;RECT r;(void)unused;GetWindowThreadProcessId(w,&pid);
 if(pid!=GetCurrentProcessId()||GetWindow(w,GW_OWNER)||!IsWindowVisible(w))return TRUE;
 GetClientRect(w,&r);
 if(r.right>=800&&r.bottom>=500){target=w;return FALSE;}return TRUE;
}
static DWORD WINAPI worker(void* unused){
 BYTE* base=(BYTE*)GetModuleHandleA(NULL);(void)unused;
 if(base!=(BYTE*)0x00400000||!has_prologue(0x00912C50)){log_line("identity_rejected");return 1;}
 EnumWindows(enum_window,0);if(!target){log_line("window_missing");return 2;}
 message=RegisterWindowMessageA("KK1.OfficialFlow.A22");
 prior=(WNDPROC)(uintptr_t)SetWindowLongPtrA(target,GWLP_WNDPROC,(LONG_PTR)window_proc);
 if(!prior){log_line("subclass_failed %lu",GetLastError());return 3;}
 InterlockedExchange(&bridge_ready,1);
#ifndef KK_OFFICIAL_FLOW_MANUAL_START
 log_line("posted %d",PostMessageA(target,message,0,0));
#else
 log_line("manual_bridge_ready");
#endif
 return 0;
}
__declspec(dllexport) DWORD WINAPI KkOfficialFlowBegin(void* unused){
 (void)unused;
 if(!InterlockedCompareExchange(&bridge_ready,0,0))return ERROR_NOT_READY;
 if(InterlockedCompareExchange(&invoked,0,0))return ERROR_ALREADY_EXISTS;
 return PostMessageA(target,message,0,0)?ERROR_SUCCESS:GetLastError();
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD why,LPVOID p){
 (void)p;
 if(why==DLL_PROCESS_ATTACH){
  HANDLE t;
#ifdef KK_OFFICIAL_FLOW_PORTABLE
  if(!init_paths(h))return FALSE;
#endif
  InitializeCriticalSection(&g_lock);g_lock_ready=1;
  DisableThreadLibraryCalls(h);
  g_veh=AddVectoredExceptionHandler(1,veh);
  log_line("loaded pid=%lu veh=%p",GetCurrentProcessId(),g_veh);
  t=CreateThread(NULL,0,worker,NULL,0,NULL);if(t)CloseHandle(t);
 }
 return TRUE;
}
