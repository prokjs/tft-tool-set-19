use std::io::{self, BufRead, Write};
use serde_json::{json, Value};
use tft_core::{dispatch, dispatch_with_data, model::GameData};

fn handle(line: &str) -> Result<Value, String> {
    let request: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
    let kind = request.get("type").and_then(Value::as_str).ok_or("A calculation request must specify its type.")?;
    let payload = request.get("payload").cloned().unwrap_or_else(|| json!({}));
    match request.get("data") {
        Some(data) => dispatch_with_data(&GameData::from_value(data.clone())?, kind, &payload),
        None => dispatch(kind, payload),
    }
}

fn main() {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    for line in io::stdin().lock().lines() {
        let reply = match line.map_err(|error| error.to_string()).and_then(|line| handle(&line)) {
            Ok(value) => json!({"ok": true, "result": value}),
            Err(error) => json!({"ok": false, "error": error}),
        };
        if writeln!(output, "{}", reply).and_then(|_| output.flush()).is_err() { break; }
    }
}
