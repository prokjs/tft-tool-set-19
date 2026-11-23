use crate::model::{names, GameData};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// Inline Collab editing filters native traits; a locked pool restores the roster.
pub fn pool(data: &GameData, payload: &Value) -> Result<Value, String> {
    let empty = json!([]);
    let selected = names(payload.get("selected").unwrap_or(&empty), "selected")?;
    for name in selected.iter().collect::<BTreeSet<_>>() {
        if !data.champions.contains_key(name) { return Err(format!("Unknown pool champion: {name}.")); }
    }
    let selected: BTreeSet<&str> = selected.iter().map(String::as_str).filter(|name| *name != "Sona").collect();
    let locked = match payload.get("locked") {
        None => false,
        Some(value) => value.as_bool().ok_or("locked must be a boolean.")?,
    };
    let _level = match payload.get("size") {
        None => 10,
        Some(value) => value.as_u64().filter(|n| *n >= 1).and_then(|n| usize::try_from(n).ok()).ok_or("level must be an integer >= 1.")?,
    };
    let mut requested = BTreeMap::new();
    if let Some(value) = payload.get("stars").filter(|value| !value.is_null()) {
        let stars = value.as_object().ok_or("stars must be a champion-to-star-level mapping.")?;
        for (name, rank) in stars {
            if !data.champions.contains_key(name) { return Err("stars contains an unknown champion.".into()); }
            let rank = rank.as_u64().filter(|n| *n >= 1).ok_or_else(|| format!("{name} stars must be an integer >= 1."))?;
            if rank > 4 { return Err("Star levels must be between 1 and 4.".into()); }
            requested.insert(name.as_str(), rank as u8);
        }
    }
    let _band=match payload.get("sona_band") {None=>"",Some(value)=>value.as_str().ok_or("sona_band must be empty or a valid band trait.")?};
    // Picker ranks are explicit inputs, separate from automatic guide progression.
    let stars: BTreeMap<_, _> = data.champions.keys().map(|name| (name.as_str(), requested.get(name.as_str()).copied().unwrap_or(1))).collect();
    let traits: BTreeSet<&str> = selected.iter().flat_map(|name| data.champions[*name].traits.iter().map(String::as_str)).filter(|name| *name != "Collab").collect();
    let exclusive: BTreeSet<&str> = selected.iter().filter_map(|name| data.champions[*name].exclusive_group.as_deref()).filter(|group| !group.is_empty()).collect();
    let mut roster: Vec<_> = data.champions.values().collect();
    roster.sort_by(|a, b| (a.cost, &a.name).cmp(&(b.cost, &b.name)));
    let eligible: Vec<&str> = roster.iter().filter(|champion| {
        selected.len() < 6 && champion.name != "Sona" && !selected.contains(champion.name.as_str())
            && !champion.traits.iter().any(|name| traits.contains(name.as_str()))
            && !champion.exclusive_group.as_deref().is_some_and(|group| !group.is_empty() && exclusive.contains(group))
    }).map(|champion| champion.name.as_str()).collect();
    let eligible_set: BTreeSet<_> = eligible.iter().copied().collect();
    let visible: Vec<&str> = roster.iter().filter(|champion| locked || champion.name == "Sona" || selected.contains(champion.name.as_str()) || eligible_set.contains(champion.name.as_str())).map(|champion| champion.name.as_str()).collect();
    Ok(json!({"eligible": eligible, "visible": visible, "stars": stars}))
}
