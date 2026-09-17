use serde::Serialize;
use std::process::Command;

#[derive(Serialize)]
struct HarnessOutput {
    status: i32,
    stdout: String,
    stderr: String,
}

#[tauri::command]
fn run_harness(args: Vec<String>) -> Result<HarnessOutput, String> {
    let binary = std::env::var("ONEC_HARNESS_BIN").unwrap_or_else(|_| "onec-harness".to_string());
    let output = Command::new(&binary)
        .args(&args)
        .output()
        .map_err(|error| format!("Failed to start {binary}: {error}"))?;

    Ok(HarnessOutput {
        status: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_harness])
        .run(tauri::generate_context!())
        .expect("error while running 1C Harness desktop");
}
