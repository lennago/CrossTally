/**
 * The app updates itself, but not in the middle of counting.
 *
 * An installed PWA is no store program: what lies in the browser is a
 * service worker, and that fetches a new version at startup. Two things
 * easily go wrong there, and both are solved here:
 *
 * 1. **An app that lies open for days** never checks. That is why
 *    'register' checks hourly and always when the app comes back from the
 *    background.
 * 2. **An automatic reload** would tear away the draft someone is typing
 *    on right now. That is why the new version waits until someone taps
 *    the notice in the bar.
 */

//: This often an app lying open checks for a new version.
const CHECK_INTERVAL = 60 * 60 * 1000;

//: The version this build was made from, written in at build time
//: ('vite.config.js'). "lokal" outside the image, as on the server.
export const APP_VERSION = __APP_VERSION__;

export const newVersion = $state({
    //: Whether a new version is ready and waits for the reload.
    ready: false,
    register,
    apply,
});

let applyFn = null;

async function register() {
    if (!("serviceWorker" in navigator)) return;
    // The virtual module comes from the PWA plugin. In the test it does not exist.
    const { registerSW } = await import("virtual:pwa-register");
    applyFn = registerSW({
        immediate: true,
        onNeedRefresh() {
            newVersion.ready = true;
        },
        onRegisteredSW(_path, registration) {
            if (!registration) return;
            const check = () => void registration.update();
            setInterval(check, CHECK_INTERVAL);
            document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "visible") check();
            });
        },
    });
}

/** Reload. Afterwards the new version runs. */
function apply() {
    newVersion.ready = false;
    if (applyFn) applyFn(true);
    else window.location.reload();
}
