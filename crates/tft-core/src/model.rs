use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

fn one() -> usize { 1 }
fn units() -> String { "units".into() }
fn ten() -> usize { 10 }
fn three() -> usize { 3 }
fn twelve() -> usize { 12 }
fn high_value() -> String { "high_value".into() }

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Champion {
    pub name: String,
    pub cost: usize,
    pub traits: Vec<String>,
    #[serde(default)] pub unlockable: bool,
    #[serde(default = "one")] pub slots: usize,
    #[serde(default)] pub role: String,
    #[serde(default)] pub attack_range: Option<f64>,
    #[serde(default)] pub exclusive_group: Option<String>,
    #[serde(default)] pub unlock_condition: String,
    #[serde(default)] pub unlock_level: String,
    #[serde(default)] pub unlock_source: String,
    #[serde(default)] pub unlock_note: String,
    #[serde(flatten)] pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TraitDef {
    pub name: String,
    pub breakpoints: Vec<usize>,
    pub unique: bool,
    #[serde(default = "units")] pub count_mode: String,
    #[serde(default)] pub description: String,
    #[serde(default)] pub description_source: String,
    #[serde(flatten)] pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug)]
pub struct GameData {
    pub raw: Value,
    pub champions: BTreeMap<String, Champion>,
    pub traits: BTreeMap<String, TraitDef>,
}

impl GameData {
    pub fn from_value(raw: Value) -> Result<Self, String> {
        let cs = raw.get("champions").and_then(Value::as_array).ok_or("Data must contain champions and traits lists.")?;
        let ts = raw.get("traits").and_then(Value::as_array).ok_or("Data must contain champions and traits lists.")?;
        let mut traits = BTreeMap::new();
        for row in ts {
            let item: TraitDef = serde_json::from_value(row.clone()).map_err(|error| error.to_string())?;
            if item.name.trim().is_empty() || traits.contains_key(&item.name) { return Err("Trait names must be nonempty and unique.".into()); }
            if item.breakpoints.is_empty() || item.breakpoints[0] == 0 || item.breakpoints.windows(2).any(|pair| pair[0] >= pair[1]) { return Err(format!("{} breakpoints must increase strictly.", item.name)); }
            if item.count_mode != "units" && item.count_mode != "stars" { return Err(format!("{} has an invalid count_mode.", item.name)); }
            traits.insert(item.name.clone(), item);
        }
        let mut champions = BTreeMap::new();
        for row in cs {
            let item: Champion = serde_json::from_value(row.clone()).map_err(|error| error.to_string())?;
            if item.name.trim().is_empty() || champions.contains_key(&item.name) { return Err("Champion names must be nonempty and unique.".into()); }
            if item.slots == 0 || item.traits.iter().any(|name| !traits.contains_key(name)) { return Err(format!("{} has invalid slots or traits.", item.name)); }
            if item.exclusive_group.as_ref().is_some_and(|group| group.trim().is_empty()) { return Err(format!("{} has an invalid exclusive_group.", item.name)); }
            champions.insert(item.name.clone(), item);
        }
        Ok(Self { raw, champions, traits })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EarlyAvailability {
    pub level: usize,
    pub stars: u8,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct OptimizeOptions {
    #[serde(default)] pub collab: Vec<String>,
    #[serde(default = "ten")] pub size: usize,
    #[serde(default)] pub locked: Vec<String>,
    #[serde(default)] pub excluded: Vec<String>,
    #[serde(default = "three")] pub min_collab: usize,
    #[serde(default = "high_value")] pub mode: String,
    #[serde(default)] pub sona_band: String,
    #[serde(default = "twelve")] pub limit: usize,
    #[serde(default)] pub stars: BTreeMap<String, u8>,
    #[serde(default)] pub early_availability: BTreeMap<String, EarlyAvailability>,
    #[serde(default)] pub completed_unlocks: BTreeMap<String, usize>,
    #[serde(default)] pub current_level: usize,
    #[serde(default)] pub no_ad_items: bool,
    #[serde(default)] pub no_ap_items: bool,
}

impl OptimizeOptions {
    pub fn from_value(value: &Value) -> Result<Self, String> { serde_json::from_value(value.clone()).map_err(|error| error.to_string()) }
    pub fn validate(&self, data: &GameData) -> Result<(), String> {
        if !(1..=11).contains(&self.size) { return Err("size must be between 1 and 11 slots.".into()); }
        if self.limit == 0 { return Err("limit must be an integer >= 1.".into()); }
        if ![0, 3, 5].contains(&self.min_collab) { return Err("min_collab must be 0, 3, or 5.".into()); }
        if !["balanced", "high_value", "synergies", "cap_out"].contains(&self.mode.as_str()) { return Err("Invalid ranking mode.".into()); }
        if self.current_level > self.size { return Err(format!("current_level must be 0..={}.", self.size)); }
        for (label, names) in [("collab", &self.collab), ("locked", &self.locked), ("excluded", &self.excluded)] {
            let mut seen = BTreeSet::new();
            for name in names {
                if name.trim().is_empty() || !seen.insert(name) { return Err(format!("{label} contains invalid or duplicate champion names.")); }
                if !data.champions.contains_key(name) { return Err(format!("Unknown {label} champion: {name}.")); }
            }
        }
        if self.locked.iter().any(|name| self.excluded.contains(name)) { return Err("A champion cannot be both locked and excluded.".into()); }
        for (name, rank) in &self.stars {
            if !data.champions.contains_key(name) || !(1..=4).contains(rank) { return Err("Invalid planned champion stars.".into()); }
        }
        let mut groups = BTreeMap::new();
        for name in &self.locked {
            if let Some(group) = &data.champions[name].exclusive_group { groups.insert(group, name); }
        }
        for (name, level) in &self.completed_unlocks {
            let c = data.champions.get(name).ok_or_else(|| format!("Unknown completed_unlocks champion: {name}."))?;
            if !c.unlockable || c.unlock_condition.trim().is_empty() || c.unlock_source.trim().is_empty() {
                return Err(format!("{name} has no valid sourced unlock task to confirm."));
            }
            if !(1..=5).contains(&c.cost) && c.cost != 7 { return Err(format!("{name} has no supported sourced acquisition route.")); }
            let minimum = if c.unlock_level == "Any" { 1 } else {
                c.unlock_level.strip_prefix("Level ").filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
                    .and_then(|s| s.parse::<usize>().ok()).ok_or_else(|| format!("{name} has an invalid published unlock level."))?
            };
            if !(1..=self.size).contains(level) || *level < minimum {
                return Err(format!("Completion for {name} must be at level {minimum}..={}; published unlock minima remain required.", self.size));
            }
            if let Some(group) = &c.exclusive_group {
                if let Some(other) = groups.insert(group, name) {
                    if other != name { return Err(format!("Confirmed unlock {name} conflicts with mutually exclusive champion {other}.")); }
                }
            }
        }
        for (name, assumption) in &self.early_availability {
            let c = data.champions.get(name).ok_or_else(|| format!("Unknown early_availability champion: {name}."))?;
            if self.excluded.contains(name) { return Err(format!("Early availability champion {name} is excluded.")); }
            if !(1..=self.size).contains(&assumption.level) || !(1..=4).contains(&assumption.stars) {
                return Err(format!("Invalid early_availability for {name}: level must be 1..={} and stars 1..=4.", self.size));
            }
            if c.slots > assumption.level { return Err(format!("Early availability for {name} needs {} board slots.", c.slots)); }
            if !(1..=5).contains(&c.cost) && !(c.cost == 7 && c.unlockable) {
                return Err(format!("{name} is unsupported for early availability."));
            }
            if c.unlockable {
                if c.unlock_condition.trim().is_empty() || c.unlock_source.trim().is_empty() {
                    return Err(format!("{name} has no valid sourced unlock task."));
                }
                let minimum = if c.unlock_level == "Any" { 1 } else {
                    c.unlock_level.strip_prefix("Level ").filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
                        .and_then(|s| s.parse::<usize>().ok()).ok_or_else(|| format!("{name} has an invalid published unlock level."))?
                };
                if assumption.level < minimum { return Err(format!("Early availability for {name} cannot precede published unlock level {minimum}.")); }
            }
            if let Some(group) = &c.exclusive_group {
                if let Some(other) = groups.insert(group, name) {
                    if other != name { return Err(format!("Early availability for {name} conflicts with mutually exclusive champion {other}.")); }
                }
            }
        }
        Ok(())
    }
}

pub fn is_tank(champion: &Champion) -> bool { champion.role.split_whitespace().any(|word| word.eq_ignore_ascii_case("tank")) }
pub fn star_text(stars: u8) -> String { "★".repeat(stars as usize) }
pub fn names(value: &Value, label: &str) -> Result<Vec<String>, String> {
    let list = value.as_array().ok_or_else(|| format!("{label} must be a list of champion names."))?;
    let mut output = Vec::with_capacity(list.len());
    let mut seen = BTreeSet::new();
    for value in list {
        let name = value.as_str().filter(|name| !name.trim().is_empty()).ok_or_else(|| format!("{label} contains an invalid name."))?;
        if !seen.insert(name) { return Err(format!("{label} must not contain duplicates.")); }
        output.push(name.to_owned());
    }
    Ok(output)
}
