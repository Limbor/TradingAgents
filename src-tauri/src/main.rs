// TradingAgents desktop shell.
//
// Owns two things:
//   1. The application window (frontend served from ../frontend/dist).
//   2. The bundled Python FastAPI backend, launched as a Tauri *sidecar*
//      binary produced by PyInstaller (see scripts/build_desktop.sh).
//
// The backend listens on 127.0.0.1:8422 (matching the frontend's baked-in
// VITE_API_BASE_URL / VITE_WS_BASE_URL). Data is written to ~/.tradingagents
// by default, so the desktop app shares state with the CLI/dev server.
//
// Lifecycle: the sidecar is spawned during `setup` and killed on app exit so
// we never leak an orphaned Python process.

use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Host/port the bundled backend binds to. Kept in sync with the frontend's
/// `.env.tauri` (VITE_API_BASE_URL / VITE_WS_BASE_URL).
const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "8422";

/// Handle to the running backend child so we can terminate it on exit.
struct BackendProcess(Mutex<Option<CommandChild>>);

fn spawn_backend(app: &tauri::App) {
    let sidecar = app
        .shell()
        .sidecar("tradingagents-backend")
        .expect("failed to create `tradingagents-backend` sidecar command")
        .env("TRADINGAGENTS_API_HOST", BACKEND_HOST)
        .env("TRADINGAGENTS_API_PORT", BACKEND_PORT);

    let (mut rx, child) = sidecar
        .spawn()
        .expect("failed to spawn the bundled backend sidecar");

    app.state::<BackendProcess>()
        .0
        .lock()
        .expect("backend process mutex poisoned")
        .replace(child);

    // Drain the sidecar's stdout/stderr so its pipes never fill up and block
    // the Python process, and surface backend logs in the parent's stderr.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[backend] terminated: {:?}", payload);
                }
                _ => {}
            }
        }
    });
}

fn kill_backend(app: &tauri::AppHandle) {
    if let Some(child) = app
        .state::<BackendProcess>()
        .0
        .lock()
        .expect("backend process mutex poisoned")
        .take()
    {
        let _ = child.kill();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            spawn_backend(app);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the TradingAgents desktop application")
        .run(|app_handle, event| match event {
            // Fired when all windows are closed or the user quits.
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                kill_backend(app_handle);
            }
            _ => {}
        });
}
