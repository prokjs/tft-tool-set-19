pub mod model;
pub mod optimizer;
pub mod planner;
pub mod guidance;
pub mod positioning;
pub mod trait_support;
pub mod item_builds;
pub mod pool;
pub mod team_code;
pub mod resources;
pub mod cap_out;
pub(crate) mod cap_items;

use model::{GameData, OptimizeOptions};
use serde_json::{json, Value};

pub const APP_NAME: &str = "prokjs's Set 19 TFT tool";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn dispatch(request_type: &str, payload: Value) -> Result<Value, String> {
    dispatch_with_data(resources::bundled_data(), request_type, &payload)
}

pub fn dispatch_with_data(data: &GameData, request_type: &str, payload: &Value) -> Result<Value, String> {
    let object = payload.as_object().ok_or("A calculation payload must be a JSON object.")?;
    let allowed: &[&str] = match request_type {
        "initialize" => &[],
        "pool" => &["selected", "locked", "size", "stars", "sona_band"],
        "recommend" => &["collab", "size", "locked", "excluded", "min_collab", "mode", "sona_band", "limit", "stars", "early_availability", "completed_unlocks", "current_level", "no_ad_items", "no_ap_items"],
        "replan" => &["result", "temporary_choices", "early_availability", "completed_unlocks", "current_level"],
        "choose_branch" => &["result", "level", "phase", "slot", "champion", "temporary_choices"],
        "damage_alternative" => &["result", "level", "phase", "no_ad_items", "no_ap_items"],
        "cap_out" => &["result", "completed_unlocks", "current_level", "item_inventory", "available_stars", "starting_board"],
        "encode" => &["champions"],
        "format" => &["result"],
        _ => return Err(format!("Unknown calculation request: {request_type}")),
    };
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(format!("Unexpected {request_type} field: {key}"));
    }
    match request_type {
        "initialize" => {
            let mut result = resources::metadata().clone();
            result["data"] = data.raw.clone();
            result["app"] = json!({"name": APP_NAME, "version": APP_VERSION, "title": format!("{APP_NAME} · v{APP_VERSION}")});
            result["bands"] = json!(optimizer::BAND_TRAITS);
            result["roles"] = Value::Object(data.champions.iter().map(|(name, champion)| (name.clone(), json!(guidance::item_profile(champion)))).collect());
            Ok(result)
        }
        "pool" => pool::pool(data, payload),
        "recommend" => {
            let options = OptimizeOptions::from_value(payload)?;
            if options.mode=="cap_out" { return Err("The cap_out ranking mode is internal; use the cap_out operation after choosing a final lineup.".into()); }
            options.validate(data)?;
            Ok(Value::Array(planner::recommend_with_buildups(data, &options)?))
        }
        "replan" => planner::replan_buildup(data, object.get("result").ok_or("Missing result for replanning.")?, object.get("temporary_choices"), object.get("early_availability"), object.get("completed_unlocks"), object.get("current_level")),
        "choose_branch" => planner::choose_branch(data, payload),
        "damage_alternative" => planner::damage_alternative(data, payload),
        "cap_out" => crate::cap_out::recommend(data, payload),
        "encode" => Ok(json!({"code": team_code::encode_team(&model::names(object.get("champions").ok_or("Missing champion roster.")?, "champions")?)?})),
        "format" => Ok(json!({"text": planner::format_buildup(object.get("result").ok_or("Missing result to format.")?, data)?})),
        _ => unreachable!(),
    }
}

#[cfg_attr(feature = "wasm", wasm_bindgen::prelude::wasm_bindgen)]
pub fn dispatch_json(request_json: &str) -> String {
    let request = serde_json::from_str::<Value>(request_json);
    let result = request.and_then(|request| {
        let request_type = request.get("type").and_then(Value::as_str).ok_or_else(|| <serde_json::Error as serde::de::Error>::custom("A calculation request must specify its type."))?;
        let payload = request.get("payload").cloned().unwrap_or_else(|| json!({}));
        Ok(dispatch(request_type, payload))
    });
    let reply = match result {
        Ok(Ok(value)) => json!({"ok": true, "result": value}),
        Ok(Err(message)) => json!({"ok": false, "error": {"name": "ValueError", "message": message}}),
        Err(error) => json!({"ok": false, "error": {"name": "ValueError", "message": error.to_string()}}),
    };
    serde_json::to_string(&reply).expect("engine responses contain finite JSON data")
}
