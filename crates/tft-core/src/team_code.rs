use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;

pub const VERSION: &str = "02";
pub const NATIVE_SLOTS: usize = 10;
pub const ID_HEX_DIGITS: usize = 3;

struct Mapping<'a> {
    set_id: &'a str,
    champions: &'a Map<String, Value>,
    unsupported: Option<&'a Map<String, Value>>,
    reverse: BTreeMap<u64, &'a str>,
}

fn validate_mapping(mapping: &Value) -> Result<Mapping<'_>, String> {
    let object = mapping.as_object().ok_or("Team Planner mapping must be a mapping document.")?;
    if object.get("format_version").and_then(Value::as_str) != Some(VERSION) { return Err("Only native Team Planner format version 02 is supported.".into()); }
    if object.get("slots").and_then(Value::as_u64) != Some(10) || object.get("id_hex_digits").and_then(Value::as_u64) != Some(3) { return Err("Version 02 requires ten slots of three hexadecimal digits.".into()); }
    let set_id = object.get("set_id").and_then(Value::as_str).filter(|id| valid_set_id(id)).ok_or("Missing or invalid native TFT set identifier.")?;
    let champions = object.get("champions").and_then(Value::as_object).filter(|map| !map.is_empty()).ok_or("Team Planner mapping has no champion IDs.")?;
    let mut reverse = BTreeMap::new();
    for (name, code) in champions {
        if name.is_empty() { return Err("Champion names in the mapping must be nonempty strings.".into()); }
        let code = code.as_u64().filter(|id| (1..=0xfff).contains(id)).ok_or_else(|| format!("Invalid native ID for {name}: expected 1..4095 (000 is padding)."))?;
        if let Some(previous) = reverse.insert(code, name.as_str()) { return Err(format!("Ambiguous native ID {code:03x}: {previous} and {name}.")); }
    }
    let unsupported = object.get("unsupported").map(|value| value.as_object().ok_or("Unsupported champion metadata must be a dictionary.")).transpose()?;
    if let Some(unsupported) = unsupported {
        for (name, detail) in unsupported {
            if !detail.is_object() || !detail.get("reason").is_some_and(json_truthy) { return Err("Unsupported champion metadata needs a name and reason.".into()); }
            if champions.contains_key(name) { return Err(format!("{name} cannot have both a supported ID and an unsupported marker.")); }
        }
    }
    Ok(Mapping { set_id, champions, unsupported, reverse })
}

fn json_truthy(value: &Value) -> bool {
    match value { Value::Null => false, Value::Bool(v) => *v, Value::Number(n) => n.as_f64() != Some(0.0), Value::String(s) => !s.is_empty(), Value::Array(a) => !a.is_empty(), Value::Object(o) => !o.is_empty() }
}

fn valid_set_id(id: &str) -> bool {
    let Some(rest) = id.strip_prefix("TFTSet") else { return false; };
    let mut parts = rest.split('_');
    let digits = parts.next().unwrap_or("");
    !digits.is_empty() && matches!(digits.as_bytes()[0], b'1'..=b'9') && digits.bytes().all(|b| b.is_ascii_digit())
        && parts.all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric()))
}

pub fn encode_team(names: &[String]) -> Result<String, String> {
    encode_team_with_mapping(names, crate::resources::team_mapping())
}

pub fn decode_team(code: &str) -> Result<Vec<String>, String> {
    decode_team_with_mapping(code, crate::resources::team_mapping())
}

/// Encode identities only; board slots, stars and items never alter native entries.
pub fn encode_team_with_mapping(names: &[String], mapping: &Value) -> Result<String, String> {
    if names.len() > NATIVE_SLOTS { return Err("Native TFT Team Planner codes support at most 10 champion entries; nothing was truncated.".into()); }
    if names.iter().collect::<BTreeSet<_>>().len() != names.len() { return Err("Native TFT Team Planner rosters cannot contain duplicate champions.".into()); }
    let mapping = validate_mapping(mapping)?;
    let mut code = String::with_capacity(32 + mapping.set_id.len());
    code.push_str(VERSION);
    for name in names {
        if let Some(detail) = mapping.unsupported.and_then(|map| map.get(name)) {
            let reason = &detail["reason"];
            return Err(format!("Cannot export {name}: {}", reason.as_str().map(str::to_owned).unwrap_or_else(|| reason.to_string())));
        }
        let id = mapping.champions.get(name).and_then(Value::as_u64).ok_or_else(|| format!("No verified native Team Planner ID for {name:?} in {}.", mapping.set_id))?;
        write!(code, "{id:03x}").expect("writing into String cannot fail");
    }
    for _ in names.len()..NATIVE_SLOTS { code.push_str("000"); }
    code.push_str(mapping.set_id);
    Ok(code)
}

/// Decode all nonzero slots, rejecting collisions rather than dropping units.
pub fn decode_team_with_mapping(code: &str, mapping: &Value) -> Result<Vec<String>, String> {
    let mapping = validate_mapping(mapping)?;
    let code = code.trim();
    let payload = code.strip_prefix(VERSION).ok_or("Only native Team Planner format version 02 is supported.")?;
    let payload = payload.strip_suffix(mapping.set_id).ok_or_else(|| format!("Native code is not for the supported roster {}.", mapping.set_id))?;
    if payload.len() != NATIVE_SLOTS * ID_HEX_DIGITS || !payload.bytes().all(|byte| byte.is_ascii_hexdigit()) { return Err("Native code must contain exactly ten three-digit hexadecimal slots.".into()); }
    let mut names = Vec::new();
    let mut seen = BTreeSet::new();
    for slot in payload.as_bytes().chunks_exact(ID_HEX_DIGITS) {
        let id = u64::from_str_radix(std::str::from_utf8(slot).expect("validated ASCII"), 16).expect("validated hex");
        if id == 0 { continue; }
        if !seen.insert(id) { return Err(format!("Duplicate champion ID {id:03x} in native roster.")); }
        let Some(name) = mapping.reverse.get(&id) else {
            let ambiguous: Vec<&str> = mapping.unsupported.into_iter().flat_map(|map| map.iter()).filter(|(_, detail)| detail.get("source_id").and_then(Value::as_u64) == Some(id)).map(|(name, _)| name.as_str()).collect();
            if !ambiguous.is_empty() { return Err(format!("Ambiguous native ID {id:03x}: {}.", ambiguous.join(", "))); }
            return Err(format!("No verified champion for native ID {id:03x} in {}.", mapping.set_id));
        };
        names.push((*name).to_owned());
    }
    Ok(names)
}
