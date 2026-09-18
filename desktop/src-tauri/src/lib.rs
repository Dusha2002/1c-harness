use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::ipc::Channel;
use tauri::State;

struct BridgeProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl Drop for BridgeProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct AppState {
    bridge: Arc<Mutex<Option<BridgeProcess>>>,
}

fn bridge_command(server: bool) -> Result<Command, String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let parent = executable.parent().ok_or("Application directory unavailable")?;
    let sidecar = parent.join(if cfg!(windows) {
        "onec-harness-bridge.exe"
    } else {
        "onec-harness-bridge"
    });

    let mut command = if sidecar.exists() {
        Command::new(sidecar)
    } else if cfg!(debug_assertions) {
        let mut cmd = Command::new(
            std::env::var("ONEC_HARNESS_PYTHON").unwrap_or_else(|_| "python".into()),
        );
        cmd.args(["-m", "onec_harness.desktop_bridge"]);
        cmd
    } else {
        return Err("Python bridge is missing. Reinstall 1C Harness.".to_string());
    };

    if server {
        command.arg("--server");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .env("PYTHONIOENCODING", "utf-8");
    Ok(command)
}

fn spawn_bridge(server: bool) -> Result<BridgeProcess, String> {
    let mut child = bridge_command(server)?.spawn().map_err(|e| e.to_string())?;
    let stdin = child.stdin.take().ok_or("Bridge stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("Bridge stdout unavailable")?;
    Ok(BridgeProcess {
        child,
        stdin,
        stdout: BufReader::new(stdout),
    })
}

fn transact(
    process: &mut BridgeProcess,
    request: &Value,
    on_event: &Channel<Value>,
) -> Result<Value, String> {
    writeln!(process.stdin, "{}", request).map_err(|e| e.to_string())?;
    process.stdin.flush().map_err(|e| e.to_string())?;

    loop {
        let mut line = String::new();
        let read = process.stdout.read_line(&mut line).map_err(|e| e.to_string())?;
        if read == 0 {
            return Err("Desktop bridge closed unexpectedly".into());
        }
        let event = serde_json::from_str::<Value>(&line)
            .map_err(|_| "Invalid bridge response".to_string())?;
        if event["type"] == "result" {
            return Ok(event["data"].clone());
        }
        if event["type"] == "error" {
            return Err(
                event["message"]
                    .as_str()
                    .unwrap_or("Bridge error")
                    .to_string(),
            );
        }
        let _ = on_event.send(event);
    }
}

fn persistent_request(
    shared: &Arc<Mutex<Option<BridgeProcess>>>,
    request: &Value,
    on_event: &Channel<Value>,
) -> Result<Value, String> {
    let mut guard = shared.lock().map_err(|_| "Desktop bridge lock poisoned")?;
    if guard.is_none() {
        *guard = Some(spawn_bridge(true)?);
    }

    let first = transact(guard.as_mut().expect("bridge initialized"), request, on_event);
    if first.is_ok() {
        return first;
    }

    // A packaged bridge can be terminated by antivirus/update/user logoff.
    // Restart once transparently instead of making the next UI click pay for it.
    *guard = None;
    *guard = Some(spawn_bridge(true)?);
    transact(guard.as_mut().expect("bridge restarted"), request, on_event)
}

fn one_shot_request(request: &Value, on_event: &Channel<Value>) -> Result<Value, String> {
    let mut process = spawn_bridge(false)?;
    transact(&mut process, request, on_event)
}

#[tauri::command]
async fn desktop_request(
    request: Value,
    on_event: Channel<Value>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let shared = state.bridge.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let op = request["op"].as_str().unwrap_or_default();
        if matches!(op, "run" | "cancel") {
            one_shot_request(&request, &on_event)
        } else {
            persistent_request(&shared, &request, &on_event)
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn pick_path(kind: String) -> Option<String> {
    let dialog = rfd::FileDialog::new();
    let selected = match kind.as_str() {
        "exe" => dialog.add_filter("1С:Предприятие", &["exe"]).pick_file(),
        "folder" => dialog.pick_folder(),
        "skill" => dialog
            .add_filter("Harness Skill", &["md", "txt"])
            .pick_file(),
        _ => None,
    };
    selected.map(|path| path.to_string_lossy().into_owned())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState {
            bridge: Arc::new(Mutex::new(None)),
        })
        .invoke_handler(tauri::generate_handler![desktop_request, pick_path])
        .run(tauri::generate_context!())
        .expect("error while running 1C Harness desktop");
}
