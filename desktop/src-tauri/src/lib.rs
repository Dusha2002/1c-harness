use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use tauri::ipc::Channel;

#[tauri::command]
async fn desktop_request(request: Value, on_event: Channel<Value>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        let parent = executable.parent().ok_or("Application directory unavailable")?;
        let sidecar = parent.join(if cfg!(windows) { "onec-harness-bridge.exe" } else { "onec-harness-bridge" });
        let mut command = if sidecar.exists() {
            Command::new(sidecar)
        } else if cfg!(debug_assertions) {
            let mut cmd = Command::new(std::env::var("ONEC_HARNESS_PYTHON").unwrap_or_else(|_| "python".into()));
            cmd.args(["-m", "onec_harness.desktop_bridge"]);
            cmd
        } else {
            return Err("Python bridge is missing. Reinstall 1C Harness.".to_string());
        };
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .env("PYTHONIOENCODING", "utf-8").spawn().map_err(|e| e.to_string())?;
        let mut input = child.stdin.take().ok_or("stdin unavailable")?;
        if let Err(error) = writeln!(input, "{}", request) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error.to_string());
        }
        drop(input);
        let output = child.stdout.take().ok_or("stdout unavailable")?;
        let mut result = None;
        let mut failure = None;
        for line in BufReader::new(output).lines() {
            let line = match line { Ok(line) => line, Err(error) => { failure = Some(error.to_string()); break; } };
            match serde_json::from_str::<Value>(&line) {
                Ok(event) if event["type"] == "result" => result = Some(event["data"].clone()),
                Ok(event) if event["type"] == "error" => failure = Some(event["message"].as_str().unwrap_or("Bridge error").into()),
                Ok(event) => { let _ = on_event.send(event); },
                Err(_) => failure = Some("Invalid bridge response".into()),
            }
        }
        let status = child.wait().map_err(|e| e.to_string())?;
        if let Some(error) = failure { return Err(error); }
        if !status.success() { return Err(format!("Bridge exited with {}", status)); }
        result.ok_or_else(|| "Bridge returned no result".into())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn pick_path(kind: String) -> Option<String> {
    let dialog = rfd::FileDialog::new();
    let selected = match kind.as_str() {
        "exe" => dialog.add_filter("1С:Предприятие", &["exe"]).pick_file(),
        "folder" => dialog.pick_folder(),
        _ => None,
    };
    selected.map(|path| path.to_string_lossy().into_owned())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![desktop_request, pick_path])
        .run(tauri::generate_context!())
        .expect("error while running 1C Harness desktop");
}
