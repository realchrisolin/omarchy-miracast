/*
 * icc-present-kick — bundled with omarchy-miracast
 *
 * Hyprland ICC only scheduleFrame/damageMonitor on the *first* capture;
 * wlr-screencopy does it every frame. Kick while a copy is pending.
 * EventBus tick only — no private-function hooks.
 *
 * Latency probe: append-only /tmp/icc-present-kick.log (monotonic ms).
 */

#define WLR_USE_UNSTABLE

#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/managers/screenshare/ScreenshareManager.hpp>
#include <hyprland/src/state/MonitorState.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/helpers/signal/Signal.hpp>
#include <aquamarine/output/Output.hpp>

#include <chrono>
#include <cstdio>
#include <string>

namespace {
CHyprSignalListener g_tickListener;
FILE*               g_log = nullptr;

uint64_t monoMs() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

void kickPendingPresents() {
    if (!g_pHyprRenderer || !Screenshare::mgr())
        return;

    for (const auto& m : State::monitorState()->monitors()) {
        if (!m || !m->m_output)
            continue;
        const auto st = Screenshare::mgr()->outputCopyFBState(m);
        if (st.pendingFrames == 0)
            continue;
        m->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_NEEDS_FRAME);
        g_pHyprRenderer->damageMonitor(m);
        if (g_log) {
            std::fprintf(g_log, "%llu kick mon=%s pending=%u sharing=%u\n",
                         (unsigned long long)monoMs(), m->m_name.c_str(), st.pendingFrames,
                         st.sharingSessions);
            std::fflush(g_log);
        }
    }
}
} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    g_log = std::fopen("/tmp/icc-present-kick.log", "a");
    if (g_log)
        std::fprintf(g_log, "%llu plugin_init\n", (unsigned long long)monoMs());

    g_tickListener = Event::bus()->m_events.tick.listen([] { kickPendingPresents(); });

    HyprlandAPI::addNotification(handle, "[icc-present-kick] v0.2.2 log=/tmp/icc-present-kick.log",
                                 CHyprColor{0.2, 0.8, 0.3, 1.0}, 2000);

    return {"icc-present-kick", "Kick ICC present while a copy is pending", "omarchy-miracast", "0.2.2"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_tickListener = nullptr;
    if (g_log) {
        std::fprintf(g_log, "%llu plugin_exit\n", (unsigned long long)monoMs());
        std::fclose(g_log);
        g_log = nullptr;
    }
}
