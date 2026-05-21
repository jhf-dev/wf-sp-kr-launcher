#define WIN32_LEAN_AND_MEAN
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <ddraw.h>
#include <stdio.h>
#include <string.h>

struct RuntimeConfig {
    char mode[16];
    LONG width;
    LONG height;
    BOOL debug;
};

struct DDProxy;

struct SurfaceProxy {
    IDirectDrawSurfaceVtbl *lpVtbl;
    IDirectDrawSurface *real;
    LONG refs;
    DDProxy *owner;
    BOOL primary;
};

struct DDProxy {
    IDirectDrawVtbl *lpVtbl;
    IDirectDraw *real;
    LONG refs;
    HWND hwnd;
    RuntimeConfig config;
};

static HMODULE g_module = NULL;
static HMODULE g_real_ddraw = NULL;

typedef HRESULT (WINAPI *DirectDrawCreateProc)(GUID FAR *, LPDIRECTDRAW FAR *, IUnknown FAR *);

static IDirectDrawVtbl g_dd_vtbl;
static IDirectDrawSurfaceVtbl g_surface_vtbl;

static DDProxy *as_dd(IDirectDraw *self) {
    return reinterpret_cast<DDProxy *>(self);
}

static SurfaceProxy *as_surface(IDirectDrawSurface *self) {
    return reinterpret_cast<SurfaceProxy *>(self);
}

static BOOL is_dd_proxy(IDirectDraw *dd) {
    return dd != NULL && *reinterpret_cast<void ***>(dd) == reinterpret_cast<void **>(&g_dd_vtbl);
}

static BOOL is_surface_proxy(IDirectDrawSurface *surface) {
    return surface != NULL && *reinterpret_cast<void ***>(surface) == reinterpret_cast<void **>(&g_surface_vtbl);
}

static IDirectDrawSurface *unwrap_surface(IDirectDrawSurface *surface) {
    if (is_surface_proxy(surface)) {
        return as_surface(surface)->real;
    }
    return surface;
}

static BOOL scaled_mode(const RuntimeConfig &config) {
    return lstrcmpiA(config.mode, "windowed") == 0 || lstrcmpiA(config.mode, "borderless") == 0;
}

static void module_dir(char *buffer, DWORD size) {
    GetModuleFileNameA(g_module, buffer, size);
    char *slash = strrchr(buffer, '\\');
    if (slash != NULL) {
        slash[1] = '\0';
    }
}

static RuntimeConfig load_config() {
    RuntimeConfig config;
    lstrcpynA(config.mode, "fullscreen", sizeof(config.mode));
    config.width = 640;
    config.height = 480;
    config.debug = FALSE;

    char dir[MAX_PATH];
    char ini[MAX_PATH];
    module_dir(dir, sizeof(dir));
    wsprintfA(ini, "%swftsp_ddraw.ini", dir);

    GetPrivateProfileStringA("wftsp_ddraw", "mode", config.mode, config.mode, sizeof(config.mode), ini);
    config.width = GetPrivateProfileIntA("wftsp_ddraw", "width", config.width, ini);
    config.height = GetPrivateProfileIntA("wftsp_ddraw", "height", config.height, ini);
    config.debug = GetPrivateProfileIntA("wftsp_ddraw", "debug", 0, ini) != 0;
    if (config.width < 320) {
        config.width = 640;
    }
    if (config.height < 240) {
        config.height = 480;
    }
    return config;
}

static void debug_log(const RuntimeConfig &config, const char *message) {
    if (!config.debug) {
        return;
    }
    char dir[MAX_PATH];
    char log_path[MAX_PATH];
    module_dir(dir, sizeof(dir));
    wsprintfA(log_path, "%swftsp_ddraw.log", dir);
    HANDLE file = CreateFileA(log_path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    DWORD written = 0;
    WriteFile(file, message, lstrlenA(message), &written, NULL);
    WriteFile(file, "\r\n", 2, &written, NULL);
    CloseHandle(file);
}

static void configure_window(DDProxy *proxy) {
    if (proxy->hwnd == NULL || !scaled_mode(proxy->config)) {
        return;
    }

    if (lstrcmpiA(proxy->config.mode, "borderless") == 0) {
        HMONITOR monitor = MonitorFromWindow(proxy->hwnd, MONITOR_DEFAULTTONEAREST);
        MONITORINFO info;
        ZeroMemory(&info, sizeof(info));
        info.cbSize = sizeof(info);
        GetMonitorInfoA(monitor, &info);
        SetWindowLongPtrA(proxy->hwnd, GWL_STYLE, WS_POPUP | WS_VISIBLE);
        SetWindowLongPtrA(proxy->hwnd, GWL_EXSTYLE, 0);
        SetWindowPos(
            proxy->hwnd,
            HWND_TOP,
            info.rcMonitor.left,
            info.rcMonitor.top,
            info.rcMonitor.right - info.rcMonitor.left,
            info.rcMonitor.bottom - info.rcMonitor.top,
            SWP_FRAMECHANGED | SWP_SHOWWINDOW
        );
    } else {
        RECT rect = {0, 0, proxy->config.width, proxy->config.height};
        DWORD style = WS_OVERLAPPEDWINDOW | WS_VISIBLE;
        DWORD ex_style = 0;
        AdjustWindowRectEx(&rect, style, FALSE, ex_style);
        SetWindowLongPtrA(proxy->hwnd, GWL_STYLE, style);
        SetWindowLongPtrA(proxy->hwnd, GWL_EXSTYLE, ex_style);
        SetWindowPos(
            proxy->hwnd,
            NULL,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            rect.right - rect.left,
            rect.bottom - rect.top,
            SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW
        );
    }
}

static RECT client_rect_screen(HWND hwnd) {
    RECT rect = {0, 0, 640, 480};
    if (hwnd != NULL && GetClientRect(hwnd, &rect)) {
        POINT points[2] = {{rect.left, rect.top}, {rect.right, rect.bottom}};
        MapWindowPoints(hwnd, NULL, points, 2);
        rect.left = points[0].x;
        rect.top = points[0].y;
        rect.right = points[1].x;
        rect.bottom = points[1].y;
    }
    return rect;
}

static RECT scale_rect(DDProxy *owner, DWORD x, DWORD y, LPRECT src_rect) {
    RECT base = client_rect_screen(owner->hwnd);
    RECT src = {0, 0, 640, 480};
    if (src_rect != NULL) {
        src = *src_rect;
    }

    LONG target_w = base.right - base.left;
    LONG target_h = base.bottom - base.top;
    RECT dst;
    dst.left = base.left + MulDiv(static_cast<int>(x), target_w, 640);
    dst.top = base.top + MulDiv(static_cast<int>(y), target_h, 480);
    dst.right = dst.left + MulDiv(src.right - src.left, target_w, 640);
    dst.bottom = dst.top + MulDiv(src.bottom - src.top, target_h, 480);
    return dst;
}

static RECT scale_dest_rect(DDProxy *owner, LPRECT dst_rect) {
    RECT base = client_rect_screen(owner->hwnd);
    if (dst_rect == NULL) {
        return base;
    }

    LONG target_w = base.right - base.left;
    LONG target_h = base.bottom - base.top;
    RECT dst;
    dst.left = base.left + MulDiv(dst_rect->left, target_w, 640);
    dst.top = base.top + MulDiv(dst_rect->top, target_h, 480);
    dst.right = base.left + MulDiv(dst_rect->right, target_w, 640);
    dst.bottom = base.top + MulDiv(dst_rect->bottom, target_h, 480);
    return dst;
}

static DWORD bltfast_to_blt_flags(DWORD flags) {
    DWORD result = DDBLT_WAIT;
    if ((flags & DDBLTFAST_SRCCOLORKEY) != 0) {
        result |= DDBLT_KEYSRC;
    }
    if ((flags & DDBLTFAST_DESTCOLORKEY) != 0) {
        result |= DDBLT_KEYDEST;
    }
    return result;
}

static SurfaceProxy *create_surface_proxy(IDirectDrawSurface *real, DDProxy *owner, BOOL primary) {
    SurfaceProxy *proxy = reinterpret_cast<SurfaceProxy *>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(SurfaceProxy)));
    if (proxy == NULL) {
        return NULL;
    }
    proxy->lpVtbl = &g_surface_vtbl;
    proxy->real = real;
    proxy->refs = 1;
    proxy->owner = owner;
    proxy->primary = primary;
    owner->real->lpVtbl->AddRef(owner->real);
    InterlockedIncrement(&owner->refs);
    return proxy;
}

static HRESULT STDMETHODCALLTYPE DD_QueryInterface(IDirectDraw *self, REFIID riid, LPVOID *out) {
    if (out == NULL) {
        return E_POINTER;
    }
    DDProxy *proxy = as_dd(self);
    if (IsEqualGUID(riid, IID_IUnknown) || IsEqualGUID(riid, IID_IDirectDraw)) {
        *out = self;
        proxy->real->lpVtbl->AddRef(proxy->real);
        InterlockedIncrement(&proxy->refs);
        return DD_OK;
    }
    return proxy->real->lpVtbl->QueryInterface(proxy->real, riid, out);
}

static ULONG STDMETHODCALLTYPE DD_AddRef(IDirectDraw *self) {
    DDProxy *proxy = as_dd(self);
    proxy->real->lpVtbl->AddRef(proxy->real);
    return InterlockedIncrement(&proxy->refs);
}

static ULONG STDMETHODCALLTYPE DD_Release(IDirectDraw *self) {
    DDProxy *proxy = as_dd(self);
    ULONG real_refs = proxy->real->lpVtbl->Release(proxy->real);
    LONG refs = InterlockedDecrement(&proxy->refs);
    if (refs == 0) {
        HeapFree(GetProcessHeap(), 0, proxy);
    }
    return real_refs;
}

static HRESULT STDMETHODCALLTYPE DD_Compact(IDirectDraw *self) {
    return as_dd(self)->real->lpVtbl->Compact(as_dd(self)->real);
}

static HRESULT STDMETHODCALLTYPE DD_CreateClipper(IDirectDraw *self, DWORD flags, LPDIRECTDRAWCLIPPER *clipper, IUnknown *outer) {
    return as_dd(self)->real->lpVtbl->CreateClipper(as_dd(self)->real, flags, clipper, outer);
}

static HRESULT STDMETHODCALLTYPE DD_CreatePalette(IDirectDraw *self, DWORD flags, LPPALETTEENTRY entries, LPDIRECTDRAWPALETTE *palette, IUnknown *outer) {
    return as_dd(self)->real->lpVtbl->CreatePalette(as_dd(self)->real, flags, entries, palette, outer);
}

static void attach_window_clipper(DDProxy *proxy, IDirectDrawSurface *surface) {
    if (proxy->hwnd == NULL || surface == NULL || !scaled_mode(proxy->config)) {
        return;
    }
    IDirectDrawClipper *clipper = NULL;
    HRESULT hr = proxy->real->lpVtbl->CreateClipper(proxy->real, 0, &clipper, NULL);
    if (FAILED(hr) || clipper == NULL) {
        return;
    }
    clipper->lpVtbl->SetHWnd(clipper, 0, proxy->hwnd);
    surface->lpVtbl->SetClipper(surface, clipper);
    clipper->lpVtbl->Release(clipper);
}

static void set_rgb565_pixel_format(DDPIXELFORMAT *format) {
    ZeroMemory(format, sizeof(*format));
    format->dwSize = sizeof(*format);
    format->dwFlags = DDPF_RGB;
    format->dwRGBBitCount = 16;
    format->dwRBitMask = 0xF800;
    format->dwGBitMask = 0x07E0;
    format->dwBBitMask = 0x001F;
}

static BOOL should_force_16bit_offscreen(DDProxy *proxy, const DDSURFACEDESC *desc, BOOL primary) {
    if (desc == NULL || primary || !scaled_mode(proxy->config)) {
        return FALSE;
    }
    if ((desc->dwFlags & (DDSD_WIDTH | DDSD_HEIGHT)) != (DDSD_WIDTH | DDSD_HEIGHT)) {
        return FALSE;
    }
    if ((desc->dwFlags & DDSD_PIXELFORMAT) != 0 && desc->ddpfPixelFormat.dwRGBBitCount == 16) {
        return FALSE;
    }
    return TRUE;
}

static HRESULT STDMETHODCALLTYPE DD_CreateSurface(IDirectDraw *self, LPDDSURFACEDESC desc, LPDIRECTDRAWSURFACE *surface, IUnknown *outer) {
    DDProxy *proxy = as_dd(self);
    BOOL primary = FALSE;
    if (desc != NULL && (desc->dwFlags & DDSD_CAPS) != 0) {
        primary = (desc->ddsCaps.dwCaps & DDSCAPS_PRIMARYSURFACE) != 0;
    }
    DDSURFACEDESC adjusted_desc;
    LPDDSURFACEDESC create_desc = desc;
    BOOL adjusted_to_16bit = should_force_16bit_offscreen(proxy, desc, primary);
    if (adjusted_to_16bit) {
        adjusted_desc = *desc;
        adjusted_desc.dwFlags |= DDSD_PIXELFORMAT;
        set_rgb565_pixel_format(&adjusted_desc.ddpfPixelFormat);
        create_desc = &adjusted_desc;
    }

    IDirectDrawSurface *real_surface = NULL;
    HRESULT hr = proxy->real->lpVtbl->CreateSurface(proxy->real, create_desc, &real_surface, outer);
    if (FAILED(hr) && adjusted_to_16bit) {
        real_surface = NULL;
        hr = proxy->real->lpVtbl->CreateSurface(proxy->real, desc, &real_surface, outer);
    }
    if (FAILED(hr) || surface == NULL || real_surface == NULL) {
        return hr;
    }
    if (primary) {
        attach_window_clipper(proxy, real_surface);
    }
    SurfaceProxy *wrapped = create_surface_proxy(real_surface, proxy, primary);
    if (wrapped == NULL) {
        real_surface->lpVtbl->Release(real_surface);
        return E_OUTOFMEMORY;
    }
    *surface = reinterpret_cast<IDirectDrawSurface *>(wrapped);
    return hr;
}

static HRESULT STDMETHODCALLTYPE DD_DuplicateSurface(IDirectDraw *self, LPDIRECTDRAWSURFACE src, LPDIRECTDRAWSURFACE *dst) {
    IDirectDrawSurface *real_dst = NULL;
    HRESULT hr = as_dd(self)->real->lpVtbl->DuplicateSurface(as_dd(self)->real, unwrap_surface(src), &real_dst);
    if (FAILED(hr) || dst == NULL || real_dst == NULL) {
        return hr;
    }
    SurfaceProxy *wrapped = create_surface_proxy(real_dst, as_dd(self), FALSE);
    if (wrapped == NULL) {
        real_dst->lpVtbl->Release(real_dst);
        return E_OUTOFMEMORY;
    }
    *dst = reinterpret_cast<IDirectDrawSurface *>(wrapped);
    return hr;
}

static HRESULT STDMETHODCALLTYPE DD_EnumDisplayModes(IDirectDraw *self, DWORD flags, LPDDSURFACEDESC desc, LPVOID context, LPDDENUMMODESCALLBACK callback) {
    return as_dd(self)->real->lpVtbl->EnumDisplayModes(as_dd(self)->real, flags, desc, context, callback);
}

static HRESULT STDMETHODCALLTYPE DD_EnumSurfaces(IDirectDraw *self, DWORD flags, LPDDSURFACEDESC desc, LPVOID context, LPDDENUMSURFACESCALLBACK callback) {
    return as_dd(self)->real->lpVtbl->EnumSurfaces(as_dd(self)->real, flags, desc, context, callback);
}

static HRESULT STDMETHODCALLTYPE DD_FlipToGDISurface(IDirectDraw *self) {
    return as_dd(self)->real->lpVtbl->FlipToGDISurface(as_dd(self)->real);
}

static HRESULT STDMETHODCALLTYPE DD_GetCaps(IDirectDraw *self, LPDDCAPS caps, LPDDCAPS hel_caps) {
    return as_dd(self)->real->lpVtbl->GetCaps(as_dd(self)->real, caps, hel_caps);
}

static HRESULT STDMETHODCALLTYPE DD_GetDisplayMode(IDirectDraw *self, LPDDSURFACEDESC desc) {
    return as_dd(self)->real->lpVtbl->GetDisplayMode(as_dd(self)->real, desc);
}

static HRESULT STDMETHODCALLTYPE DD_GetFourCCCodes(IDirectDraw *self, LPDWORD count, LPDWORD codes) {
    return as_dd(self)->real->lpVtbl->GetFourCCCodes(as_dd(self)->real, count, codes);
}

static HRESULT STDMETHODCALLTYPE DD_GetGDISurface(IDirectDraw *self, LPDIRECTDRAWSURFACE *surface) {
    return as_dd(self)->real->lpVtbl->GetGDISurface(as_dd(self)->real, surface);
}

static HRESULT STDMETHODCALLTYPE DD_GetMonitorFrequency(IDirectDraw *self, LPDWORD frequency) {
    return as_dd(self)->real->lpVtbl->GetMonitorFrequency(as_dd(self)->real, frequency);
}

static HRESULT STDMETHODCALLTYPE DD_GetScanLine(IDirectDraw *self, LPDWORD line) {
    return as_dd(self)->real->lpVtbl->GetScanLine(as_dd(self)->real, line);
}

static HRESULT STDMETHODCALLTYPE DD_GetVerticalBlankStatus(IDirectDraw *self, LPBOOL status) {
    return as_dd(self)->real->lpVtbl->GetVerticalBlankStatus(as_dd(self)->real, status);
}

static HRESULT STDMETHODCALLTYPE DD_Initialize(IDirectDraw *self, GUID *guid) {
    return as_dd(self)->real->lpVtbl->Initialize(as_dd(self)->real, guid);
}

static HRESULT STDMETHODCALLTYPE DD_RestoreDisplayMode(IDirectDraw *self) {
    DDProxy *proxy = as_dd(self);
    if (scaled_mode(proxy->config)) {
        return DD_OK;
    }
    return proxy->real->lpVtbl->RestoreDisplayMode(proxy->real);
}

static HRESULT STDMETHODCALLTYPE DD_SetCooperativeLevel(IDirectDraw *self, HWND hwnd, DWORD flags) {
    DDProxy *proxy = as_dd(self);
    proxy->hwnd = hwnd;
    if (scaled_mode(proxy->config)) {
        HRESULT hr = proxy->real->lpVtbl->SetCooperativeLevel(proxy->real, hwnd, DDSCL_NORMAL);
        configure_window(proxy);
        return hr;
    }
    return proxy->real->lpVtbl->SetCooperativeLevel(proxy->real, hwnd, flags);
}

static HRESULT STDMETHODCALLTYPE DD_SetDisplayMode(IDirectDraw *self, DWORD width, DWORD height, DWORD bpp) {
    DDProxy *proxy = as_dd(self);
    if (scaled_mode(proxy->config)) {
        configure_window(proxy);
        return DD_OK;
    }
    return proxy->real->lpVtbl->SetDisplayMode(proxy->real, width, height, bpp);
}

static HRESULT STDMETHODCALLTYPE DD_WaitForVerticalBlank(IDirectDraw *self, DWORD flags, HANDLE event_handle) {
    return as_dd(self)->real->lpVtbl->WaitForVerticalBlank(as_dd(self)->real, flags, event_handle);
}

static HRESULT STDMETHODCALLTYPE Surface_QueryInterface(IDirectDrawSurface *self, REFIID riid, LPVOID *out) {
    if (out == NULL) {
        return E_POINTER;
    }
    SurfaceProxy *proxy = as_surface(self);
    if (IsEqualGUID(riid, IID_IUnknown) || IsEqualGUID(riid, IID_IDirectDrawSurface)) {
        *out = self;
        proxy->real->lpVtbl->AddRef(proxy->real);
        InterlockedIncrement(&proxy->refs);
        return DD_OK;
    }
    return proxy->real->lpVtbl->QueryInterface(proxy->real, riid, out);
}

static ULONG STDMETHODCALLTYPE Surface_AddRef(IDirectDrawSurface *self) {
    SurfaceProxy *proxy = as_surface(self);
    proxy->real->lpVtbl->AddRef(proxy->real);
    return InterlockedIncrement(&proxy->refs);
}

static ULONG STDMETHODCALLTYPE Surface_Release(IDirectDrawSurface *self) {
    SurfaceProxy *proxy = as_surface(self);
    ULONG real_refs = proxy->real->lpVtbl->Release(proxy->real);
    LONG refs = InterlockedDecrement(&proxy->refs);
    if (refs == 0) {
        IDirectDraw *owner = reinterpret_cast<IDirectDraw *>(proxy->owner);
        owner->lpVtbl->Release(owner);
        HeapFree(GetProcessHeap(), 0, proxy);
    }
    return real_refs;
}

static HRESULT STDMETHODCALLTYPE Surface_AddAttachedSurface(IDirectDrawSurface *self, LPDIRECTDRAWSURFACE attached) {
    return as_surface(self)->real->lpVtbl->AddAttachedSurface(as_surface(self)->real, unwrap_surface(attached));
}

static HRESULT STDMETHODCALLTYPE Surface_AddOverlayDirtyRect(IDirectDrawSurface *self, LPRECT rect) {
    return as_surface(self)->real->lpVtbl->AddOverlayDirtyRect(as_surface(self)->real, rect);
}

static HRESULT STDMETHODCALLTYPE Surface_Blt(IDirectDrawSurface *self, LPRECT dst_rect, LPDIRECTDRAWSURFACE src, LPRECT src_rect, DWORD flags, LPDDBLTFX fx) {
    SurfaceProxy *proxy = as_surface(self);
    IDirectDrawSurface *real_src = unwrap_surface(src);
    if (proxy->primary && scaled_mode(proxy->owner->config) && real_src != NULL) {
        RECT scaled = scale_dest_rect(proxy->owner, dst_rect);
        return proxy->real->lpVtbl->Blt(proxy->real, &scaled, real_src, src_rect, flags | DDBLT_WAIT, fx);
    }
    return proxy->real->lpVtbl->Blt(proxy->real, dst_rect, real_src, src_rect, flags, fx);
}

static HRESULT STDMETHODCALLTYPE Surface_BltBatch(IDirectDrawSurface *self, LPDDBLTBATCH batch, DWORD count, DWORD flags) {
    return as_surface(self)->real->lpVtbl->BltBatch(as_surface(self)->real, batch, count, flags);
}

static HRESULT STDMETHODCALLTYPE Surface_BltFast(IDirectDrawSurface *self, DWORD x, DWORD y, LPDIRECTDRAWSURFACE src, LPRECT src_rect, DWORD flags) {
    SurfaceProxy *proxy = as_surface(self);
    IDirectDrawSurface *real_src = unwrap_surface(src);
    if (proxy->primary && scaled_mode(proxy->owner->config) && real_src != NULL) {
        RECT dst = scale_rect(proxy->owner, x, y, src_rect);
        return proxy->real->lpVtbl->Blt(proxy->real, &dst, real_src, src_rect, bltfast_to_blt_flags(flags), NULL);
    }
    return proxy->real->lpVtbl->BltFast(proxy->real, x, y, real_src, src_rect, flags);
}

static HRESULT STDMETHODCALLTYPE Surface_DeleteAttachedSurface(IDirectDrawSurface *self, DWORD flags, LPDIRECTDRAWSURFACE attached) {
    return as_surface(self)->real->lpVtbl->DeleteAttachedSurface(as_surface(self)->real, flags, unwrap_surface(attached));
}

static HRESULT STDMETHODCALLTYPE Surface_EnumAttachedSurfaces(IDirectDrawSurface *self, LPVOID context, LPDDENUMSURFACESCALLBACK callback) {
    return as_surface(self)->real->lpVtbl->EnumAttachedSurfaces(as_surface(self)->real, context, callback);
}

static HRESULT STDMETHODCALLTYPE Surface_EnumOverlayZOrders(IDirectDrawSurface *self, DWORD flags, LPVOID context, LPDDENUMSURFACESCALLBACK callback) {
    return as_surface(self)->real->lpVtbl->EnumOverlayZOrders(as_surface(self)->real, flags, context, callback);
}

static HRESULT STDMETHODCALLTYPE Surface_Flip(IDirectDrawSurface *self, LPDIRECTDRAWSURFACE target, DWORD flags) {
    return as_surface(self)->real->lpVtbl->Flip(as_surface(self)->real, unwrap_surface(target), flags);
}

static HRESULT STDMETHODCALLTYPE Surface_GetAttachedSurface(IDirectDrawSurface *self, LPDDSCAPS caps, LPDIRECTDRAWSURFACE *surface) {
    IDirectDrawSurface *real_surface = NULL;
    HRESULT hr = as_surface(self)->real->lpVtbl->GetAttachedSurface(as_surface(self)->real, caps, &real_surface);
    if (FAILED(hr) || surface == NULL || real_surface == NULL) {
        return hr;
    }
    SurfaceProxy *wrapped = create_surface_proxy(real_surface, as_surface(self)->owner, FALSE);
    if (wrapped == NULL) {
        real_surface->lpVtbl->Release(real_surface);
        return E_OUTOFMEMORY;
    }
    *surface = reinterpret_cast<IDirectDrawSurface *>(wrapped);
    return hr;
}

static HRESULT STDMETHODCALLTYPE Surface_GetBltStatus(IDirectDrawSurface *self, DWORD flags) {
    return as_surface(self)->real->lpVtbl->GetBltStatus(as_surface(self)->real, flags);
}

static HRESULT STDMETHODCALLTYPE Surface_GetCaps(IDirectDrawSurface *self, LPDDSCAPS caps) {
    return as_surface(self)->real->lpVtbl->GetCaps(as_surface(self)->real, caps);
}

static HRESULT STDMETHODCALLTYPE Surface_GetClipper(IDirectDrawSurface *self, LPDIRECTDRAWCLIPPER *clipper) {
    return as_surface(self)->real->lpVtbl->GetClipper(as_surface(self)->real, clipper);
}

static HRESULT STDMETHODCALLTYPE Surface_GetColorKey(IDirectDrawSurface *self, DWORD flags, LPDDCOLORKEY key) {
    return as_surface(self)->real->lpVtbl->GetColorKey(as_surface(self)->real, flags, key);
}

static HRESULT STDMETHODCALLTYPE Surface_GetDC(IDirectDrawSurface *self, HDC *dc) {
    return as_surface(self)->real->lpVtbl->GetDC(as_surface(self)->real, dc);
}

static HRESULT STDMETHODCALLTYPE Surface_GetFlipStatus(IDirectDrawSurface *self, DWORD flags) {
    return as_surface(self)->real->lpVtbl->GetFlipStatus(as_surface(self)->real, flags);
}

static HRESULT STDMETHODCALLTYPE Surface_GetOverlayPosition(IDirectDrawSurface *self, LPLONG x, LPLONG y) {
    return as_surface(self)->real->lpVtbl->GetOverlayPosition(as_surface(self)->real, x, y);
}

static HRESULT STDMETHODCALLTYPE Surface_GetPalette(IDirectDrawSurface *self, LPDIRECTDRAWPALETTE *palette) {
    return as_surface(self)->real->lpVtbl->GetPalette(as_surface(self)->real, palette);
}

static HRESULT STDMETHODCALLTYPE Surface_GetPixelFormat(IDirectDrawSurface *self, LPDDPIXELFORMAT format) {
    return as_surface(self)->real->lpVtbl->GetPixelFormat(as_surface(self)->real, format);
}

static HRESULT STDMETHODCALLTYPE Surface_GetSurfaceDesc(IDirectDrawSurface *self, LPDDSURFACEDESC desc) {
    return as_surface(self)->real->lpVtbl->GetSurfaceDesc(as_surface(self)->real, desc);
}

static HRESULT STDMETHODCALLTYPE Surface_Initialize(IDirectDrawSurface *self, LPDIRECTDRAW dd, LPDDSURFACEDESC desc) {
    IDirectDraw *real_dd = is_dd_proxy(dd) ? as_dd(dd)->real : dd;
    return as_surface(self)->real->lpVtbl->Initialize(as_surface(self)->real, real_dd, desc);
}

static HRESULT STDMETHODCALLTYPE Surface_IsLost(IDirectDrawSurface *self) {
    return as_surface(self)->real->lpVtbl->IsLost(as_surface(self)->real);
}

static HRESULT STDMETHODCALLTYPE Surface_Lock(IDirectDrawSurface *self, LPRECT rect, LPDDSURFACEDESC desc, DWORD flags, HANDLE event_handle) {
    return as_surface(self)->real->lpVtbl->Lock(as_surface(self)->real, rect, desc, flags, event_handle);
}

static HRESULT STDMETHODCALLTYPE Surface_ReleaseDC(IDirectDrawSurface *self, HDC dc) {
    return as_surface(self)->real->lpVtbl->ReleaseDC(as_surface(self)->real, dc);
}

static HRESULT STDMETHODCALLTYPE Surface_Restore(IDirectDrawSurface *self) {
    return as_surface(self)->real->lpVtbl->Restore(as_surface(self)->real);
}

static HRESULT STDMETHODCALLTYPE Surface_SetClipper(IDirectDrawSurface *self, LPDIRECTDRAWCLIPPER clipper) {
    return as_surface(self)->real->lpVtbl->SetClipper(as_surface(self)->real, clipper);
}

static HRESULT STDMETHODCALLTYPE Surface_SetColorKey(IDirectDrawSurface *self, DWORD flags, LPDDCOLORKEY key) {
    return as_surface(self)->real->lpVtbl->SetColorKey(as_surface(self)->real, flags, key);
}

static HRESULT STDMETHODCALLTYPE Surface_SetOverlayPosition(IDirectDrawSurface *self, LONG x, LONG y) {
    return as_surface(self)->real->lpVtbl->SetOverlayPosition(as_surface(self)->real, x, y);
}

static HRESULT STDMETHODCALLTYPE Surface_SetPalette(IDirectDrawSurface *self, LPDIRECTDRAWPALETTE palette) {
    return as_surface(self)->real->lpVtbl->SetPalette(as_surface(self)->real, palette);
}

static HRESULT STDMETHODCALLTYPE Surface_Unlock(IDirectDrawSurface *self, LPVOID data) {
    return as_surface(self)->real->lpVtbl->Unlock(as_surface(self)->real, data);
}

static HRESULT STDMETHODCALLTYPE Surface_UpdateOverlay(IDirectDrawSurface *self, LPRECT src_rect, LPDIRECTDRAWSURFACE dst, LPRECT dst_rect, DWORD flags, LPDDOVERLAYFX fx) {
    return as_surface(self)->real->lpVtbl->UpdateOverlay(as_surface(self)->real, src_rect, unwrap_surface(dst), dst_rect, flags, fx);
}

static HRESULT STDMETHODCALLTYPE Surface_UpdateOverlayDisplay(IDirectDrawSurface *self, DWORD flags) {
    return as_surface(self)->real->lpVtbl->UpdateOverlayDisplay(as_surface(self)->real, flags);
}

static HRESULT STDMETHODCALLTYPE Surface_UpdateOverlayZOrder(IDirectDrawSurface *self, DWORD flags, LPDIRECTDRAWSURFACE ref) {
    return as_surface(self)->real->lpVtbl->UpdateOverlayZOrder(as_surface(self)->real, flags, unwrap_surface(ref));
}

static void init_vtables() {
    g_dd_vtbl.QueryInterface = DD_QueryInterface;
    g_dd_vtbl.AddRef = DD_AddRef;
    g_dd_vtbl.Release = DD_Release;
    g_dd_vtbl.Compact = DD_Compact;
    g_dd_vtbl.CreateClipper = DD_CreateClipper;
    g_dd_vtbl.CreatePalette = DD_CreatePalette;
    g_dd_vtbl.CreateSurface = DD_CreateSurface;
    g_dd_vtbl.DuplicateSurface = DD_DuplicateSurface;
    g_dd_vtbl.EnumDisplayModes = DD_EnumDisplayModes;
    g_dd_vtbl.EnumSurfaces = DD_EnumSurfaces;
    g_dd_vtbl.FlipToGDISurface = DD_FlipToGDISurface;
    g_dd_vtbl.GetCaps = DD_GetCaps;
    g_dd_vtbl.GetDisplayMode = DD_GetDisplayMode;
    g_dd_vtbl.GetFourCCCodes = DD_GetFourCCCodes;
    g_dd_vtbl.GetGDISurface = DD_GetGDISurface;
    g_dd_vtbl.GetMonitorFrequency = DD_GetMonitorFrequency;
    g_dd_vtbl.GetScanLine = DD_GetScanLine;
    g_dd_vtbl.GetVerticalBlankStatus = DD_GetVerticalBlankStatus;
    g_dd_vtbl.Initialize = DD_Initialize;
    g_dd_vtbl.RestoreDisplayMode = DD_RestoreDisplayMode;
    g_dd_vtbl.SetCooperativeLevel = DD_SetCooperativeLevel;
    g_dd_vtbl.SetDisplayMode = DD_SetDisplayMode;
    g_dd_vtbl.WaitForVerticalBlank = DD_WaitForVerticalBlank;

    g_surface_vtbl.QueryInterface = Surface_QueryInterface;
    g_surface_vtbl.AddRef = Surface_AddRef;
    g_surface_vtbl.Release = Surface_Release;
    g_surface_vtbl.AddAttachedSurface = Surface_AddAttachedSurface;
    g_surface_vtbl.AddOverlayDirtyRect = Surface_AddOverlayDirtyRect;
    g_surface_vtbl.Blt = Surface_Blt;
    g_surface_vtbl.BltBatch = Surface_BltBatch;
    g_surface_vtbl.BltFast = Surface_BltFast;
    g_surface_vtbl.DeleteAttachedSurface = Surface_DeleteAttachedSurface;
    g_surface_vtbl.EnumAttachedSurfaces = Surface_EnumAttachedSurfaces;
    g_surface_vtbl.EnumOverlayZOrders = Surface_EnumOverlayZOrders;
    g_surface_vtbl.Flip = Surface_Flip;
    g_surface_vtbl.GetAttachedSurface = Surface_GetAttachedSurface;
    g_surface_vtbl.GetBltStatus = Surface_GetBltStatus;
    g_surface_vtbl.GetCaps = Surface_GetCaps;
    g_surface_vtbl.GetClipper = Surface_GetClipper;
    g_surface_vtbl.GetColorKey = Surface_GetColorKey;
    g_surface_vtbl.GetDC = Surface_GetDC;
    g_surface_vtbl.GetFlipStatus = Surface_GetFlipStatus;
    g_surface_vtbl.GetOverlayPosition = Surface_GetOverlayPosition;
    g_surface_vtbl.GetPalette = Surface_GetPalette;
    g_surface_vtbl.GetPixelFormat = Surface_GetPixelFormat;
    g_surface_vtbl.GetSurfaceDesc = Surface_GetSurfaceDesc;
    g_surface_vtbl.Initialize = Surface_Initialize;
    g_surface_vtbl.IsLost = Surface_IsLost;
    g_surface_vtbl.Lock = Surface_Lock;
    g_surface_vtbl.ReleaseDC = Surface_ReleaseDC;
    g_surface_vtbl.Restore = Surface_Restore;
    g_surface_vtbl.SetClipper = Surface_SetClipper;
    g_surface_vtbl.SetColorKey = Surface_SetColorKey;
    g_surface_vtbl.SetOverlayPosition = Surface_SetOverlayPosition;
    g_surface_vtbl.SetPalette = Surface_SetPalette;
    g_surface_vtbl.Unlock = Surface_Unlock;
    g_surface_vtbl.UpdateOverlay = Surface_UpdateOverlay;
    g_surface_vtbl.UpdateOverlayDisplay = Surface_UpdateOverlayDisplay;
    g_surface_vtbl.UpdateOverlayZOrder = Surface_UpdateOverlayZOrder;
}

static HMODULE load_real_ddraw() {
    if (g_real_ddraw != NULL) {
        return g_real_ddraw;
    }
    char system_dir[MAX_PATH];
    char path[MAX_PATH];
    GetSystemDirectoryA(system_dir, sizeof(system_dir));
    wsprintfA(path, "%s\\ddraw.dll", system_dir);
    g_real_ddraw = LoadLibraryA(path);
    return g_real_ddraw;
}

extern "C" __declspec(dllexport)
HRESULT WINAPI DirectDrawCreate(GUID FAR *guid, LPDIRECTDRAW FAR *dd, IUnknown FAR *outer) {
    HMODULE real = load_real_ddraw();
    if (real == NULL) {
        return E_FAIL;
    }
    DirectDrawCreateProc proc = reinterpret_cast<DirectDrawCreateProc>(GetProcAddress(real, "DirectDrawCreate"));
    if (proc == NULL) {
        return E_FAIL;
    }

    IDirectDraw *real_dd = NULL;
    HRESULT hr = proc(guid, &real_dd, outer);
    if (FAILED(hr) || dd == NULL || real_dd == NULL) {
        return hr;
    }

    DDProxy *proxy = reinterpret_cast<DDProxy *>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(DDProxy)));
    if (proxy == NULL) {
        real_dd->lpVtbl->Release(real_dd);
        return E_OUTOFMEMORY;
    }
    proxy->lpVtbl = &g_dd_vtbl;
    proxy->real = real_dd;
    proxy->refs = 1;
    proxy->hwnd = NULL;
    proxy->config = load_config();
    debug_log(proxy->config, "DirectDrawCreate wrapped");
    *dd = reinterpret_cast<IDirectDraw *>(proxy);
    return hr;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = instance;
        init_vtables();
        DisableThreadLibraryCalls(instance);
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_real_ddraw != NULL) {
            FreeLibrary(g_real_ddraw);
            g_real_ddraw = NULL;
        }
    }
    return TRUE;
}
