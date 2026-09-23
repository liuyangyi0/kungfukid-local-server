#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static DWORD find_process_id(const wchar_t* exe_name) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W pe;
    ZeroMemory(&pe, sizeof(pe));
    pe.dwSize = sizeof(pe);
    DWORD pid = 0;
    if (Process32FirstW(snap, &pe)) {
        do {
            if (_wcsicmp(pe.szExeFile, exe_name) == 0) {
                pid = pe.th32ProcessID;
                break;
            }
        } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    return pid;
}

int wmain(int argc, wchar_t** argv) {
    const wchar_t* process_name = L"Client.exe";
    const wchar_t* dll_path = argc >= 2 ? argv[1] : L"C:\\KK-Lab\\kk_net_hook.dll";
    DWORD pid = 0;
    if (argc >= 3) {
        if (_wcsicmp(argv[2], L"--pid") == 0) {
            if (argc < 4) {
                fwprintf(stderr, L"usage: %ls [dll_path] [--pid pid | process_name]\n", argv[0]);
                return 1;
            }
            wchar_t* end = NULL;
            unsigned long parsed = wcstoul(argv[3], &end, 10);
            if (!argv[3][0] || !end || *end || parsed == 0 || parsed > 0xFFFFFFFFUL) {
                fwprintf(stderr, L"invalid pid: %ls\n", argv[3]);
                return 1;
            }
            pid = (DWORD)parsed;
        } else {
            process_name = argv[2];
        }
    }

    DWORD full_len = GetFullPathNameW(dll_path, 0, NULL, NULL);
    if (!full_len) {
        fwprintf(stderr, L"GetFullPathNameW failed: %lu\n", GetLastError());
        return 2;
    }
    wchar_t* full_path = (wchar_t*)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, full_len * sizeof(wchar_t));
    if (!full_path) return 3;
    GetFullPathNameW(dll_path, full_len, full_path, NULL);
    DWORD attributes = GetFileAttributesW(full_path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
        fwprintf(stderr, L"DLL not found: %ls\n", full_path);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 3;
    }

    if (!pid) pid = find_process_id(process_name);
    if (!pid) {
        fwprintf(stderr, L"process not found: %ls\n", process_name);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 4;
    }

    HANDLE process = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE,
        pid
    );
    if (!process) {
        fwprintf(stderr, L"OpenProcess(%lu) failed: %lu\n", pid, GetLastError());
        HeapFree(GetProcessHeap(), 0, full_path);
        return 5;
    }

    SIZE_T bytes = (wcslen(full_path) + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(process, NULL, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) {
        fwprintf(stderr, L"VirtualAllocEx failed: %lu\n", GetLastError());
        CloseHandle(process);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 6;
    }
    SIZE_T written = 0;
    if (!WriteProcessMemory(process, remote, full_path, bytes, &written) || written != bytes) {
        fwprintf(stderr, L"WriteProcessMemory failed: %lu written=%llu expected=%llu\n", GetLastError(), (unsigned long long)written, (unsigned long long)bytes);
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 7;
    }

    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    FARPROC load_library = GetProcAddress(kernel32, "LoadLibraryW");
    HANDLE thread = CreateRemoteThread(process, NULL, 0, (LPTHREAD_START_ROUTINE)load_library, remote, 0, NULL);
    if (!thread) {
        fwprintf(stderr, L"CreateRemoteThread failed: %lu\n", GetLastError());
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 8;
    }

    DWORD wait_result = WaitForSingleObject(thread, 10000);
    if (wait_result != WAIT_OBJECT_0) {
        fwprintf(stderr, L"remote LoadLibrary wait failed: result=%lu error=%lu pid=%lu\n", wait_result, GetLastError(), pid);
        CloseHandle(thread);
        CloseHandle(process);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 10;
    }
    DWORD code = 0;
    if (!GetExitCodeThread(thread, &code)) {
        fwprintf(stderr, L"GetExitCodeThread failed: %lu\n", GetLastError());
        CloseHandle(thread);
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        CloseHandle(process);
        HeapFree(GetProcessHeap(), 0, full_path);
        return 11;
    }
    wprintf(L"injected pid=%lu dll=%ls load_result=0x%08lX\n", pid, full_path, code);
    CloseHandle(thread);
    VirtualFreeEx(process, remote, 0, MEM_RELEASE);
    CloseHandle(process);
    HeapFree(GetProcessHeap(), 0, full_path);
    return code ? 0 : 9;
}
