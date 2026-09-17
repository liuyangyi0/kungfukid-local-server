// Read-only runtime observation for the natural SDK login path; no callbacks,
// selectors, gameplay hooks, or client-state writes. VM isolated copy only.
#include <windows.h>
#include <cstdio>
static DWORD WINAPI observe(void*){
    wchar_t image[32768];DWORD n=GetModuleFileNameW(nullptr,image,32768);
    if(!n||n==32768)return 1;
    wchar_t* end=wcsrchr(image,L'\\');if(!end)return 2;end[1]=0;
    if(wcsstr(image,L"C:\\KK-Lab\\sdo-native-client-")!=image)return 3;
    wcscat_s(image,L"kk-roleprop-ready.txt");
    if(GetModuleHandleW(nullptr)!=(HMODULE)0x400000)return 4;
    for(int i=0;i<1200;++i){
        DWORD table=0;SIZE_T read=0;
        if(ReadProcessMemory(GetCurrentProcess(),(void*)0x17C86F0,&table,4,&read)&&read==4&&table){
            char text[32];int len=sprintf_s(text,"0x%08lX\r\n",table);DWORD written;
            HANDLE f=CreateFileW(image,GENERIC_WRITE,FILE_SHARE_READ,nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
            if(f!=INVALID_HANDLE_VALUE){WriteFile(f,text,len,&written,nullptr);CloseHandle(f);return 0;}
        }Sleep(250);
    }return 5;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){
    if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(h);HANDLE t=CreateThread(nullptr,0,observe,nullptr,0,nullptr);if(t)CloseHandle(t);}return TRUE;
}
