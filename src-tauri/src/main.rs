#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[derive(Debug, Serialize)]
struct EngineError {
    name: &'static str,
    message: String,
}

#[tauri::command]
async fn engine_dispatch(request_type: String, payload: Value) -> Result<Value, EngineError> {
    // CPU-intensive searches never run on the WebView or async runtime threads.
    tauri::async_runtime::spawn_blocking(move || tft_core::dispatch(&request_type, payload))
        .await
        .map_err(|error| EngineError {
            name: "RuntimeError",
            message: format!("The native calculation task failed: {error}"),
        })?
        .map_err(|message| EngineError {
            name: "ValueError",
            message,
        })
}

#[tauri::command]
fn copy_text(app: tauri::AppHandle, text: String) -> Result<(), String> {
    app.clipboard().write_text(text).map_err(|error| error.to_string())
}

#[tauri::command]
async fn save_json(app: tauri::AppHandle, contents: String) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app.dialog().file()
            .set_title("Save team plans as JSON")
            .set_file_name("collab-teams.json")
            .add_filter("JSON", &["json"])
            .blocking_save_file() else {
                return Ok(false);
            };
        let path = file.into_path().map_err(|error| error.to_string())?;
        std::fs::write(path, contents).map_err(|error| error.to_string())?;
        Ok(true)
    })
    .await
    .map_err(|error| format!("The JSON export task failed: {error}"))?
}

#[tauri::command]
async fn open_external(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|error| error.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("Only HTTP and HTTPS source links can be opened externally.".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        app.opener().open_url(url, None::<&str>).map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| format!("The source link could not be opened: {error}"))?
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![engine_dispatch, copy_text, save_json, open_external])
        .run(tauri::generate_context!())
        .expect("Unable to start prokjs's Set 19 TFT tool");
}
