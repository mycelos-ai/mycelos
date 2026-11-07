#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Write;
use std::process::{Command, Stdio};
use serde_json::Value;
use tauri_plugin_dialog::DialogExt;

fn allowed(action: &str) -> bool {
    matches!(action, "status" | "pair" | "add" | "discover" | "pause" | "resume" | "start" | "stop" | "rules" | "remove" | "sync" | "history" | "disconnect" | "open-server")
}

fn control(action: &str, payload: Option<Value>) -> Result<Value, String> {
    if !allowed(action) { return Err("Unzulässiger Auftrag".into()); }
    let data = serde_json::to_vec(&payload.unwrap_or(Value::Null)).map_err(|_| "Ungültige Eingabe")?;
    if data.len() > 16384 { return Err("Die Eingabe ist zu groß".into()); }
    // An administrator can point the shell at a specific installed collector.
    // The web view cannot change the executable, environment, or arguments.
    let executable = std::env::var_os("MYCELOS_COLLECTOR_EXECUTABLE").unwrap_or_else(|| {
        let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
        let installed = home.map(|h| h.join(".local/bin/mycelos-collector"));
        installed.filter(|p| p.is_file()).map(|p| p.into_os_string()).unwrap_or_else(|| "mycelos-collector".into())
    });
    let mut child = Command::new(executable).arg(action)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| "Der Sammler fehlt. Installiere zuerst das Python-Paket mycelos.")?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(&data).map_err(|_| "Der Sammler nimmt keine Eingabe an")?;
    }
    let result = child.wait_with_output().map_err(|_| "Der Sammler antwortet nicht")?;
    if !result.status.success() { return Err("Der Auftrag ist fehlgeschlagen. Prüfe die Einladung und die Ordnerfreigabe.".into()); }
    serde_json::from_slice(&result.stdout).map_err(|_| "Die Antwort des Sammlers ist ungültig".into())
}

#[tauri::command]
async fn collector(action: String, payload: Option<Value>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || control(&action, payload)).await.map_err(|_| "Der Auftrag ist fehlgeschlagen")?
}

#[tauri::command]
async fn choose_source(app: tauri::AppHandle, file: bool) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file();
        let result = if file { dialog.blocking_pick_file() } else { dialog.blocking_pick_folder() };
        result.map(|p| p.to_string())
    }).await.map_err(|_| "Die Ordnerauswahl ist fehlgeschlagen".into())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![collector, choose_source])
        .run(tauri::generate_context!())
        .expect("Cannot start collector window");
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn arbitrary_process_commands_are_denied() {
        for action in ["run", "sh", "status; touch /tmp/file", "../status", ""] {
            assert!(!allowed(action));
            assert!(control(action, None).is_err());
        }
        for action in ["status", "pair", "add", "pause", "resume", "start", "stop", "discover", "rules", "remove", "sync", "history", "disconnect", "open-server"] {
            assert!(allowed(action));
        }
    }
}
