// TradingAgents desktop shell.
//
// Owns two things:
//   1. The application window (frontend served from ../frontend/dist).
//   2. The bundled Python FastAPI backend, launched as a Tauri *sidecar*
//      binary produced by PyInstaller (see scripts/build_desktop.sh).
//
// The backend listens on a per-launch random loopback port and requires a
// per-launch bearer token. Data is written to ~/.tradingagents by default, so
// the desktop app shares state with the CLI/dev server.
//
// Lifecycle: the sidecar is spawned during `setup` and killed on app exit so
// we never leak an orphaned Python process.

use std::{net::TcpListener, sync::Mutex};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

const BACKEND_HOST: &str = "127.0.0.1";
const DESKTOP_ALLOWED_ORIGINS: &str =
    "tauri://localhost,http://tauri.localhost,https://tauri.localhost";

#[derive(Clone, Serialize)]
struct BackendConnection {
    api_origin: String,
    ws_origin: String,
    token: String,
}

/// Per-launch connection details plus the child handle used during shutdown.
struct BackendRuntime {
    connection: BackendConnection,
    process: Mutex<Option<CommandChild>>,
}

impl BackendRuntime {
    fn new(port: u16) -> Self {
        // Two independent v4 UUIDs provide a 256-bit, non-persisted secret.
        let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        Self {
            connection: BackendConnection {
                api_origin: format!("http://{BACKEND_HOST}:{port}"),
                ws_origin: format!("ws://{BACKEND_HOST}:{port}"),
                token,
            },
            process: Mutex::new(None),
        }
    }
}

fn available_loopback_port() -> u16 {
    TcpListener::bind((BACKEND_HOST, 0))
        .and_then(|listener| listener.local_addr())
        .expect("failed to allocate a loopback port for the backend")
        .port()
}

#[tauri::command]
fn backend_connection(runtime: State<'_, BackendRuntime>) -> BackendConnection {
    runtime.connection.clone()
}

fn spawn_backend(app: &tauri::App) {
    let runtime = app.state::<BackendRuntime>();
    let port = runtime
        .connection
        .api_origin
        .rsplit(':')
        .next()
        .expect("backend origin has no port");
    let sidecar = app
        .shell()
        .sidecar("tradingagents-backend")
        .expect("failed to create `tradingagents-backend` sidecar command")
        .env("TRADINGAGENTS_API_HOST", BACKEND_HOST)
        .env("TRADINGAGENTS_API_PORT", port)
        .env("TRADINGAGENTS_API_AUTH_TOKEN", &runtime.connection.token)
        .env("TRADINGAGENTS_API_ALLOWED_ORIGINS", DESKTOP_ALLOWED_ORIGINS);

    let (mut rx, child) = sidecar
        .spawn()
        .expect("failed to spawn the bundled backend sidecar");

    runtime
        .process
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
        .state::<BackendRuntime>()
        .process
        .lock()
        .expect("backend process mutex poisoned")
        .take()
    {
        let _ = child.kill();
    }
}

fn main() {
    let backend_runtime = BackendRuntime::new(available_loopback_port());
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(backend_runtime)
        .invoke_handler(tauri::generate_handler![backend_connection])
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
