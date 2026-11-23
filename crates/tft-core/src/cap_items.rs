//! Finite physical-item accounting for cap-outs only.
//!
//! Compatibility is a category/role heuristic, not a damage simulation. Preparation
//! performs all JSON/catalog work. The candidate hot path is deterministic greedy
//! matching with stack-only capacity/uniqueness state: every instance is visited
//! once, retained holders are locked, and no candidate can reuse an item.
use crate::model::{Champion, GameData};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

#[derive(Deserialize)]
struct Catalog {
    source: String,
    snapshot_date: String,
    source_sha256: String,
    items: BTreeMap<String, CatalogItem>,
}
#[derive(Deserialize)]
struct CatalogItem {
    id: String,
    kind: String,
    tags: Vec<String>,
    equippable: bool,
    component: bool,
    slot_cost: u8,
    unique_per_champion: bool,
    allowed_holders: Vec<String>,
}
static CATALOG: LazyLock<Catalog> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/item_catalog.json"))
        .expect("bundled item catalog is valid")
});

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InventoryInput {
    id: String,
    item: String,
    holder: Option<String>,
}
#[derive(Clone, Serialize)]
struct ItemInstance {
    id: String,
    item: String,
    holder: Option<String>,
    source_target: Option<String>,
    source_holder: Option<String>,
    provenance: Value,
}

pub(crate) struct ItemContext {
    collected: bool,
    instances: Vec<ItemInstance>,
    alternatives: Vec<Value>,
    notes: Vec<String>,
}

#[derive(Clone, Copy)]
enum Fit { Tank, Magic, Attack, Caster, AttackSpeed, Melee, Hybrid, Utility, Unmodelled }

struct PreparedItem {
    weights: Vec<f64>,
    source_holder: Option<usize>,
    slots: u8,
    unique: bool,
    identity: usize,
}

pub(crate) struct PreparedItems {
    items: Vec<PreparedItem>,
    order: Vec<usize>,
    strengths: Vec<f64>,
}

impl ItemContext {
    pub(crate) fn from_source(data: &GameData, source: &Value, inventory: Option<&Value>) -> Result<Self, String> {
        let roster: BTreeSet<_> = crate::guidance::names(source).into_iter().collect();
        let collected = inventory.is_some_and(|value| !value.is_null());
        let mut context = Self { collected, instances: vec![], alternatives: vec![], notes: vec![] };
        if collected {
            let entries: Vec<InventoryInput> = serde_json::from_value(inventory.unwrap().clone())
                .map_err(|error| format!("Invalid item_inventory: {error}"))?;
            let mut ids = BTreeSet::new();
            let mut capacity: BTreeMap<&str, u8> = BTreeMap::new();
            let mut unique = BTreeSet::new();
            for entry in &entries {
                if entry.id.trim().is_empty() || entry.id.trim() != entry.id || !ids.insert(entry.id.as_str()) {
                    return Err("Every physical item needs a nonempty, unique id without surrounding whitespace.".into());
                }
                let item = CATALOG.items.get(&entry.item)
                    .ok_or_else(|| format!("Unknown collected item: {}", entry.item))?;
                if let Some(holder) = &entry.holder {
                    if !data.champions.contains_key(holder) || !roster.contains(holder) {
                        return Err(format!("Item holder {holder} must be present on the confirmed starting board; use bench for an unheld item."));
                    }
                    if !item.equippable {
                        return Err(format!("{} is a consumable or non-equipment item; keep it on the item bench until actually used.", entry.item));
                    }
                    if !item.allowed_holders.is_empty() && !item.allowed_holders.contains(holder) {
                        return Err(format!("{} can only be held by {} according to its source rules.", entry.item, item.allowed_holders.join(", ")));
                    }
                    let used = capacity.entry(holder).or_default();
                    *used += item.slot_cost;
                    if *used > 3 {
                        return Err(format!("Item holder {holder} exceeds three item slots (including items that consume all three slots)."));
                    }
                    if item.unique_per_champion && !unique.insert((holder, &entry.item)) {
                        return Err(format!("{} is unique per champion and cannot be equipped twice on {holder}.", entry.item));
                    }
                }
                context.instances.push(ItemInstance {
                    id: entry.id.clone(), item: entry.item.clone(), holder: entry.holder.clone(),
                    source_target: entry.holder.clone(), source_holder: entry.holder.clone(),
                    provenance: json!({"kind":"collected_inventory","label":"User-confirmed physical item","catalog_source":CATALOG.source}),
                });
            }
            context.notes.push("Collected item inventory is authoritative; no recommended items are added or crafted.".into());
        } else {
            let mut planned = source.clone();
            if !planned["planned_item_builds"].is_array() {
                crate::guidance::annotate_result(&mut planned, data)?;
            }
            let builds = planned["planned_item_builds"].as_array().cloned().unwrap_or_default();
            // AD/AP/Flex recommendations for one holder describe alternatives,
            // not multiple physical sets. Keep provenance for all alternatives.
            let mut groups: BTreeMap<String, Vec<Value>> = BTreeMap::new();
            for build in builds {
                let key = build["holder"].as_str().or_else(|| build["target"].as_str())
                    .ok_or_else(|| "A planned item build needs a source holder or target.".to_owned())?;
                groups.entry(key.to_owned()).or_default().push(build);
            }
            for (group, mut alternatives) in groups {
                let profile = data.champions.get(&group).map_or("unknown", crate::guidance::item_profile);
                alternatives.sort_by_key(|build| {
                    let kind = build["kind"].as_str().unwrap_or("");
                    (u8::from(kind != profile), u8::from(kind != "flex"), kind.to_owned())
                });
                let selected = &alternatives[0];
                let list = selected["items"].as_array().ok_or_else(|| format!("Planned item build for {group} has no item list."))?;
                let mut used = 0;
                let mut selected_ids = vec![];
                let mut selected_unique = BTreeSet::new();
                for name in list {
                    let name = name.as_str().ok_or_else(|| "Planned item names must be strings.".to_owned())?;
                    // Older published plans may differ in capitalization only.
                    let (canonical, item) = CATALOG.items.get_key_value(name)
                        .or_else(|| CATALOG.items.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)))
                        .ok_or_else(|| format!("Planned item is absent from the sourced Set 19 catalog: {name}"))?;
                    if !item.equippable {
                        return Err(format!("Planned loadout contains a non-equippable item: {name}"));
                    }
                    // Published lists can contain alternatives beyond a legal
                    // loadout. Explicitly select one finite <=3-slot loadout.
                    if used + item.slot_cost > 3 || (item.unique_per_champion && !selected_unique.insert(canonical.clone())) {
                        continue;
                    }
                    used += item.slot_cost;
                    let holder = selected["holder"].as_str().filter(|name| roster.contains(*name)).map(str::to_owned);
                    let id = format!("planned-{}", context.instances.len() + 1);
                    selected_ids.push(id.clone());
                    context.instances.push(ItemInstance {
                        id, item: canonical.clone(), holder: holder.clone(),
                        source_target: selected["source_target"].as_str().or_else(|| selected["target"].as_str()).map(str::to_owned),
                        source_holder: selected["source_holder"].as_str().or_else(|| selected["holder"].as_str()).map(str::to_owned),
                        provenance: selected.clone(),
                    });
                }
                context.alternatives.push(json!({"source_holder":group,"selected_item_ids":selected_ids,
                    "selection":"One legal source loadout; AD/AP/Flex and excess published suggestions are alternatives, not extra copies.",
                    "alternatives":alternatives}));
            }
            context.notes.push("Planned-item what-if: one finite original-comp loadout per holder, not a claim about collected items. AD/AP/Flex suggestions are mutually exclusive alternatives.".into());
        }
        context.notes.push("Item fit uses sourced categories and combat roles, not DPS simulation. Special effects, emblems, random items, crafting, and consumable use are not simulated.".into());
        if context.instances.iter().any(|item| item.source_holder.is_some()) {
            context.notes.push("Items stay on a retained starting holder. Selling that holder releases the exact items; otherwise use an item remover manually before requesting a different allocation.".into());
        }
        Ok(context)
    }

    pub(crate) fn prepare(&self, data: &GameData, rank: impl Fn(&str) -> u8) -> PreparedItems {
        let units: Vec<_> = data.champions.values().collect();
        let identities: BTreeMap<_, _> = CATALOG.items.keys().enumerate().map(|(index, name)| (name.as_str(), index)).collect();
        let items: Vec<_> = self.instances.iter().map(|instance| {
            let item = &CATALOG.items[&instance.item];
            let fit = item_fit(item);
            PreparedItem {
                weights: units.iter().map(|unit| if item.equippable { affinity(fit, unit) * 10.0 } else { 0.0 }).collect(),
                source_holder: instance.holder.as_deref().and_then(|name| units.iter().position(|unit| unit.name == name)),
                slots: item.slot_cost, unique: item.unique_per_champion, identity: identities[instance.item.as_str()],
            }
        }).collect();
        let mut order: Vec<_> = (0..items.len()).collect();
        // Scarce categories before generic utility, then strongest compatibility;
        // all inventory ordering happens once, never in the beam hot path.
        order.sort_by(|&left, &right| {
            let scarcity = |index: usize| items[index].weights.iter().filter(|&&weight| weight >= 8.0).count();
            let best = |index: usize| items[index].weights.iter().copied().fold(0.0f64, f64::max);
            scarcity(left).cmp(&scarcity(right)).then_with(|| best(right).total_cmp(&best(left))).then(left.cmp(&right))
        });
        let strengths = units.iter().map(|unit| crate::guidance::planned_unit_strength(unit, rank(&unit.name))).collect();
        PreparedItems { items, order, strengths }
    }

    pub(crate) fn metadata(&self) -> Value {
        json!({"item_mode":if self.collected {"collected"} else {"planned"},"item_budget":self.instances,
            "item_source_alternatives":self.alternatives,"item_catalog_source":CATALOG.source,
            "item_catalog_snapshot":CATALOG.snapshot_date,"item_catalog_sha256":CATALOG.source_sha256,
            "item_fit_model":"Finite scarcity-first role/category matching; retained holders locked; three slots per unit; special effects not simulated."})
    }

    pub(crate) fn notes(&self) -> Vec<String> { self.notes.clone() }

    /// Plan final recipients once, then carry that same physical ledger through
    /// every phase. Outgoing holders keep items until sold; unavailable final
    /// recipients leave released items on bench, never on an invented proxy.
    pub(crate) fn annotate_boards(&self, from: &mut Value, target: &mut Value, phases: &mut [Value], data: &GameData) -> Result<(), String> {
        // Allocate for the copies available at acquisition, not future guide upgrades.
        let prepared = self.prepare(data, |name| target["acquisition_stars"][name].as_u64().or_else(|| target["planned_stars"][name].as_u64()).unwrap_or(1) as u8);
        let units: Vec<_> = data.champions.keys().cloned().collect();
        let target_ids = board_ids(target, &units)?;
        let mut destinations = vec![None; self.instances.len()];
        prepared.assign(&target_ids, |item, recipient, _| destinations[item] = Some(target_ids[recipient]));
        let mut holders: Vec<_> = self.instances.iter().map(|instance| instance.holder.clone()).collect();
        self.annotate_board(from, &holders, &destinations, &units, &prepared, data)?;
        for phase in phases {
            let fielded: BTreeSet<_> = crate::guidance::names(&phase["board"]).into_iter().collect();
            let mut moves = vec![];
            for (index, holder) in holders.iter_mut().enumerate() {
                let before = holder.clone();
                if holder.as_ref().is_some_and(|name| !fielded.contains(name)) { *holder = None; }
                if holder.is_none() {
                    if let Some(destination) = destinations[index].map(|id| &units[id]).filter(|name| fielded.contains(*name)) {
                        *holder = Some(destination.clone());
                    }
                }
                if *holder != before {
                    moves.push(self.transfer(index, before.as_deref(), holder.as_deref()));
                }
            }
            phase["item_transfers"] = json!(moves);
            self.annotate_board(&mut phase["board"], &holders, &destinations, &units, &prepared, data)?;
        }
        // A no-exchange result still allocates bench items onto the final board.
        // A held item cannot move between two retained recipients here.
        let fielded: BTreeSet<_> = crate::guidance::names(target).into_iter().collect();
        let mut final_moves = vec![];
        for (index, holder) in holders.iter_mut().enumerate() {
            let before = holder.clone();
            if holder.as_ref().is_some_and(|name| !fielded.contains(name)) { *holder = None; }
            if holder.is_none() { *holder = destinations[index].map(|id| units[id].clone()); }
            if *holder != before { final_moves.push(self.transfer(index, before.as_deref(), holder.as_deref())); }
        }
        self.annotate_board(target, &holders, &destinations, &units, &prepared, data)?;
        target["item_transfers"] = json!(final_moves);
        Ok(())
    }

    fn transfer(&self, index: usize, outgoing: Option<&str>, incoming: Option<&str>) -> Value {
        let instance = &self.instances[index];
        json!({"item_ids":[instance.id],"items":[instance.item],"outgoing":outgoing,"incoming":incoming,
            "kind":"physical_items","method":if outgoing.is_some() {"sell_outgoing"} else {"equip_from_bench"},
            "condition":if outgoing.is_some() {"Sell the outgoing holder to release these exact items; equip only after the recipient is acquired."} else {"Equip these exact bench items only after the recipient is acquired."}})
    }

    fn annotate_board(&self, board: &mut Value, holders: &[Option<String>], destinations: &[Option<usize>], units: &[String], prepared: &PreparedItems, data: &GameData) -> Result<(), String> {
        let ids = board_ids(board, units)?;
        let fielded: BTreeSet<_> = ids.iter().map(|&id| units[id].as_str()).collect();
        let mut groups: BTreeMap<(String, Option<usize>), Vec<usize>> = BTreeMap::new();
        let mut bench = vec![];
        let mut fit = 0.0;
        let mut occupied: BTreeMap<String, (u8, f64)> = BTreeMap::new();
        let mut unique = BTreeSet::new();
        for (index, instance) in self.instances.iter().enumerate() {
            if let Some(holder) = &holders[index] {
                if !fielded.contains(holder.as_str()) { return Err(format!("Cannot equip {} on absent holder {holder}.", instance.id)); }
                let item = &prepared.items[index];
                if item.slots == 0 { return Err("Non-equipment items cannot be equipped by a cap transition.".into()); }
                let used = occupied.entry(holder.clone()).or_default();
                used.0 += item.slots;
                if used.0 > 3 || (item.unique && !unique.insert((holder, item.identity))) {
                    return Err(format!("Finite item assignment exceeds capacity or uniqueness on {holder}."));
                }
                let unit = units.binary_search(holder).map_err(|_| format!("Unknown item recipient: {holder}"))?;
                fit += item.weights[unit];
                // Only genuinely compatible equipped items qualify a main holder,
                // using the same threshold as finite assignment. Custody alone
                // does not turn an off-role item into a tank/carry loadout.
                if item.weights[unit] >= 5.0 { used.1 += item.weights[unit]; }
                groups.entry((holder.clone(), destinations[index])).or_default().push(index);
            } else {
                let mut entry = serde_json::to_value(instance).map_err(|error| error.to_string())?;
                entry["holder"] = Value::Null;
                entry["target"] = destinations[index].map(|id| json!(units[id])).unwrap_or(Value::Null);
                entry["bench_reason"] = json!(if CATALOG.items[&instance.item].component {"Component retained without assuming crafting or a new equip."} else if prepared.items[index].slots == 0 {"Consumable or special non-equipment item: retained without assuming use."} else if destinations[index].is_some() {"Keep on bench until the intended recipient is acquired."} else {"No compatible free item slot or unmodelled special effect; keep this physical item on bench."});
                bench.push(entry);
            }
        }
        let builds: Vec<_> = groups.iter().map(|((holder, destination), indices)| {
            let profile = data.champions.get(holder).map_or("unknown", crate::guidance::item_profile);
            let sources: Vec<_> = indices.iter().map(|&index| json!({"item_id":self.instances[index].id,
                "source_target":self.instances[index].source_target,"source_holder":self.instances[index].source_holder,
                "provenance":self.instances[index].provenance})).collect();
            let source_holder = indices.first().and_then(|&index| self.instances[index].source_holder.as_deref());
            let source_target = indices.first().and_then(|&index| self.instances[index].source_target.as_deref());
            json!({"kind":profile,"target":destination.map(|id| &units[id]),"holder":holder,
                "item_ids":indices.iter().map(|&index| &self.instances[index].id).collect::<Vec<_>>(),
                "items":indices.iter().map(|&index| &self.instances[index].item).collect::<Vec<_>>(),
                "source_holder":source_holder,"source_target":source_target,"item_sources":sources,
                "source_kind":if self.collected {"collected_inventory"} else {"inherited_source_plan"},
                "label":if self.collected {"Collected items"} else {"Original planned items (what-if)"},
                "note":"Exact physical items, not this recipient's published best-in-slot. Special effects are not simulated.",
                "holder_archetype":data.champions.get(holder).map(crate::guidance::item_archetype),"holder_match":"physical_assignment"})
        }).collect();
        board["planned_item_builds"] = json!(builds);
        let mut item_holders = json!({"tank":null,"ad":null,"ap":null,"flex":null});
        let mut best: [Option<(usize, f64, f64)>; 4] = [None; 4];
        for &unit in &ids {
            let holder = &units[unit];
            let Some(&(_, equipped_fit)) = occupied.get(holder).filter(|(_, fit)| *fit > 0.0) else { continue; };
            let champion = &data.champions[holder];
            let profile = crate::guidance::item_profile(champion);
            let Some(kind) = crate::guidance::KINDS.iter().position(|&kind| kind == profile) else { continue; };
            // Each phase's planned_stars describes that phase, not the eventual
            // guide target or a newly acquired unit's hypothetical future rank.
            let stars = board["planned_stars"][holder].as_u64().unwrap_or(1) as u8;
            let strength = crate::guidance::planned_unit_strength(champion, stars);
            if best[kind].is_none_or(|(current, current_strength, current_fit)| {
                strength.total_cmp(&current_strength)
                    .then_with(|| equipped_fit.total_cmp(&current_fit))
                    .then_with(|| units[current].cmp(holder)).is_gt()
            }) { best[kind] = Some((unit, strength, equipped_fit)); }
        }
        for (kind, selected) in crate::guidance::KINDS.into_iter().zip(best) {
            if let Some((unit, _, _)) = selected { item_holders[kind] = json!(units[unit]); }
        }
        board["item_holders"] = item_holders;
        board["item_mode"] = json!(if self.collected {"collected"} else {"planned"});
        board["item_bench"] = json!(bench);
        board["item_fit"] = json!({"score":fit,"potential_score":prepared.score(&ids),"assigned":self.instances.len()-bench.len(),
            "bench":bench.len(),"budget":self.instances.len(),"model":"Finite physical items; role/category heuristic, not DPS; retained holders cannot transfer freely.",
            "unmodelled_special_effects":true});
        Ok(())
    }
}

fn board_ids(board: &Value, units: &[String]) -> Result<Vec<usize>, String> {
    let names = board["champions"].as_array().ok_or_else(|| "An item board needs a champions array.".to_owned())?;
    if names.len() > 11 { return Err("Finite cap item matching supports at most eleven fielded champions.".into()); }
    let mut ids = Vec::with_capacity(names.len());
    for name in names {
        let name = name.as_str().ok_or_else(|| "Item board champion names must be strings.".to_owned())?;
        let id = units.binary_search_by(|unit| unit.as_str().cmp(name)).map_err(|_| format!("Unknown item board champion: {name}"))?;
        if ids.contains(&id) { return Err(format!("Duplicate item board champion: {name}")); }
        ids.push(id);
    }
    Ok(ids)
}

impl PreparedItems {
    pub(crate) fn score(&self, ids: &[usize]) -> f64 {
        let mut score = 0.0;
        self.assign(ids, |_, _, weight| score += weight);
        score
    }

    fn assign(&self, ids: &[usize], mut emit: impl FnMut(usize, usize, f64)) {
        if ids.len() > 11 { return; }
        let mut capacity = [0u8; 11];
        let mut equipped = [[usize::MAX; 3]; 11];
        let mut counts = [0usize; 11];
        // Held physical items reserve slots before any bench/released item.
        for &index in &self.order {
            let item = &self.items[index];
            if item.slots == 0 { continue; }
            if let Some(recipient) = item.source_holder.and_then(|holder| ids.iter().position(|&id| id == holder)) {
                capacity[recipient] += item.slots;
                equipped[recipient][counts[recipient]] = item.identity;
                counts[recipient] += 1;
                emit(index, recipient, item.weights[ids[recipient]]);
            }
        }
        for &index in &self.order {
            let item = &self.items[index];
            if item.slots == 0 || item.source_holder.is_some_and(|holder| ids.contains(&holder)) { continue; }
            let mut best = None;
            for (recipient, &unit) in ids.iter().enumerate() {
                if capacity[recipient] + item.slots > 3 || (item.unique && equipped[recipient][..counts[recipient]].contains(&item.identity)) { continue; }
                let weight = item.weights[unit];
                // Compatibility first, then current acquisition-rank strength.
                // Only equal-strength recipients use loadout concentration and
                // canonical ordering; a cheap filler must not win by its name.
                if weight < 5.0 { continue; }
                if best.is_none_or(|(previous, previous_weight): (usize, f64)|
                    weight.total_cmp(&previous_weight)
                        .then_with(|| self.strengths[unit].total_cmp(&self.strengths[ids[previous]]))
                        .then_with(|| capacity[recipient].cmp(&capacity[previous]))
                        .then_with(|| ids[previous].cmp(&unit)).is_gt()) {
                    best = Some((recipient, weight));
                }
            }
            if let Some((recipient, weight)) = best {
                capacity[recipient] += item.slots;
                equipped[recipient][counts[recipient]] = item.identity;
                counts[recipient] += 1;
                emit(index, recipient, weight);
            }
        }
    }
}

/// Normal planned recommendations may omit unavailable offensive categories;
/// this never filters an actual cap inventory or changes physical ownership.
pub(crate) fn item_allowed_for_plan(name: &str, no_ad: bool, no_ap: bool) -> bool {
    if !no_ad && !no_ap { return true; }
    let Some(item) = CATALOG.items.get(name)
        .or_else(|| CATALOG.items.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, item)| item)) else { return false; };
    if item.component { return false; }
    match item_fit(item) {
        Fit::Attack | Fit::AttackSpeed | Fit::Melee => !no_ad,
        Fit::Magic => !no_ap,
        Fit::Caster | Fit::Hybrid => !(no_ad && no_ap),
        Fit::Tank | Fit::Utility => true,
        Fit::Unmodelled => false,
    }
}

fn item_fit(item: &CatalogItem) -> Fit {
    if item.component { return Fit::Unmodelled; }
    // Source names distinguish mechanics that share broad Attack/Magic tags.
    // Radiant variants preserve the same fit, not invented combat statistics.
    let id = item.id.to_ascii_lowercase();
    if ["bloodthirster", "steraksgage", "titansresolve", "edgeofnight"].iter().any(|name| id.contains(name)) { return Fit::Melee; }
    if ["guinsoosrageblade", "kraken", "navori", "fishbones"].iter().any(|name| id.contains(name)) { return Fit::AttackSpeed; }
    if ["spearofshojin", "bluebuff", "dawncore"].iter().any(|name| id.contains(name)) { return Fit::Caster; }
    if matches!(item.kind.as_str(), "emblem" | "artifact" | "misc") { return Fit::Unmodelled; }
    if item.kind == "support" || item.slot_cost == 3 { return Fit::Utility; }
    let has = |suffix: &str| item.tags.iter().any(|tag| tag == &format!("Item.Equippable.Category.{suffix}"));
    let (attack, magic, tank) = (has("Attack"), has("Magic"), has("Tank"));
    match (attack, magic, tank) {
        (_, _, true) => Fit::Tank,
        (true, true, false) => Fit::Hybrid,
        (true, false, false) => Fit::Attack,
        (false, true, false) => Fit::Magic,
        _ => Fit::Unmodelled,
    }
}

fn affinity(fit: Fit, champion: &Champion) -> f64 {
    let role = champion.role.to_ascii_lowercase();
    let magic = role.starts_with("magic ");
    let attack = role.starts_with("attack ");
    let caster = role.split_whitespace().any(|word| word == "caster");
    let marksman = role.split_whitespace().any(|word| word == "marksman");
    let melee = role.split_whitespace().any(|word| matches!(word, "fighter" | "assassin"));
    let tank = crate::model::is_tank(champion);
    let damage = !tank && (magic || attack);
    match fit {
        Fit::Tank => if tank { 1.0 } else if melee { 0.55 } else { 0.1 },
        Fit::Magic => if magic && caster { 1.0 } else if magic && damage { 0.65 } else { 0.05 },
        Fit::Attack => if attack && damage { 1.0 } else if melee { 0.55 } else { 0.05 },
        Fit::Caster => if caster { 1.0 } else if damage && !melee { 0.6 } else { 0.1 },
        Fit::AttackSpeed => if marksman { 1.0 } else if damage && !caster { 0.65 } else { 0.2 },
        Fit::Melee => if melee { 1.0 } else if damage { 0.3 } else { 0.1 },
        Fit::Hybrid => if damage { 0.85 } else { 0.1 },
        Fit::Utility => 0.5,
        // Preserve special items truthfully without inventing a category or an
        // advantage from a mechanic that this solver does not understand.
        Fit::Unmodelled => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn data() -> GameData {
        GameData::from_value(json!({"traits":[],"champions":[
            {"name":"Caster A","cost":4,"traits":[],"role":"Magic Caster"},
            {"name":"Caster B","cost":4,"traits":[],"role":"Magic Caster"},
            {"name":"Caster C","cost":4,"traits":[],"role":"Magic Caster"},
            {"name":"Caster D","cost":4,"traits":[],"role":"Magic Caster"},
            {"name":"Fighter","cost":4,"traits":[],"role":"Attack Fighter"},
            {"name":"Tank","cost":4,"traits":[],"role":"Magic Tank"}
        ]})).unwrap()
    }

    fn ids(data: &GameData, names: &[&str]) -> Vec<usize> {
        names.iter().map(|name| data.champions.keys().position(|unit| unit.as_str() == *name).unwrap()).collect()
    }

    #[test]
    fn matching_does_not_reuse_one_mage_trio_on_multiple_carries() {
        let data = data();
        let source = json!({"champions":[],"planned_item_builds":[]});
        let inventory = json!([
            {"id":"shojin","item":"Spear of Shojin","holder":null},
            {"id":"jg","item":"Jeweled Gauntlet","holder":null},
            {"id":"cap","item":"Rabadon's Deathcap","holder":null}
        ]);
        let prepared = ItemContext::from_source(&data, &source, Some(&inventory)).unwrap().prepare(&data, |_| 1);
        assert_eq!(prepared.score(&ids(&data, &["Caster A"])), 30.0);
        assert_eq!(prepared.score(&ids(&data, &["Caster A","Caster B","Caster C"])), 30.0);
        assert_eq!(prepared.score(&ids(&data, &["Fighter"])), 0.0);
    }

    #[test]
    fn fourth_actual_loadout_is_scored_not_clipped_to_three_sets() {
        let data = data();
        let source = json!({"champions":[],"planned_item_builds":[]});
        let inventory = json!((0..12).map(|index| json!({
            "id":format!("copy-{index}"),"item":"Rabadon's Deathcap","holder":null
        })).collect::<Vec<_>>());
        let prepared = ItemContext::from_source(&data, &source, Some(&inventory)).unwrap().prepare(&data, |_| 1);
        assert_eq!(prepared.score(&ids(&data, &["Caster A","Caster B","Caster C"])), 90.0);
        assert_eq!(prepared.score(&ids(&data, &["Caster A","Caster B","Caster C","Caster D"])), 120.0);
    }

    #[test]
    fn retained_custody_reserves_slots_before_free_bench_items() {
        let data = data();
        let source = json!({"champions":["Caster A"],"planned_item_builds":[]});
        let inventory = json!([
            {"id":"gloves","item":"Thief's Gloves","holder":"Caster A"},
            {"id":"cap","item":"Rabadon's Deathcap","holder":null}
        ]);
        let prepared = ItemContext::from_source(&data, &source, Some(&inventory)).unwrap().prepare(&data, |_| 1);
        assert_eq!(prepared.score(&ids(&data, &["Caster A"])), 5.0);
        assert_eq!(prepared.score(&ids(&data, &["Caster A","Caster B"])), 15.0);
    }

    #[test]
    fn source_categories_filter_only_planned_offense() {
        assert!(!item_allowed_for_plan("Bloodthirster", true, false));
        assert!(!item_allowed_for_plan("Jeweled Gauntlet", false, true));
        assert!(item_allowed_for_plan("Spear of Shojin", true, false));
        assert!(!item_allowed_for_plan("Spear of Shojin", true, true));
        assert!(item_allowed_for_plan("Warmog's Armor", true, true));
    }

    #[test]
    fn released_viego_items_prefer_stronger_compatible_recipients_without_breaking_custody() {
        let data = crate::resources::bundled_data();
        let inventory = json!([
            {"id":"viego-edge","item":"Edge of Night","holder":"Viego"},
            {"id":"viego-justice","item":"Hand Of Justice","holder":"Viego"},
            {"id":"viego-infinity","item":"Infinity Edge","holder":"Viego"}
        ]);
        let context = ItemContext::from_source(data, &json!({"champions":["Viego","Jax","Zeri"]}), Some(&inventory)).unwrap();
        for (jax_rank, acquired_rank, include_vi, expected_edge, expected_justice) in [
            (2, 2, true, "Vi", "Vi"), (3, 2, true, "Jax", "Jax"),
            (2, 1, true, "Jax", "Jax"), (2, 2, false, "Jax", "Zeri"),
        ] {
            let mut from = json!({"champions":["Viego","Jax","Zeri"],"planned_stars":{"Viego":2,"Jax":jax_rank,"Zeri":2}});
            let mut target = json!({"champions":if include_vi {vec!["Jax","Vi","Zeri"]} else {vec!["Jax","Zeri"]},
                "planned_stars":{"Jax":jax_rank,"Vi":2,"Zeri":2},
                "acquisition_stars":{"Jax":jax_rank,"Vi":acquired_rank,"Zeri":acquired_rank}});
            let mut phases = vec![json!({"board":target.clone()})];
            context.annotate_boards(&mut from, &mut target, &mut phases, data).unwrap();
            let recipient = |board:&Value, id:&str| board["planned_item_builds"].as_array().unwrap().iter()
                .find(|build| build["item_ids"].as_array().unwrap().iter().any(|item| item==id))
                .unwrap()["holder"].as_str().unwrap().to_owned();
            for id in ["viego-edge","viego-justice","viego-infinity"] { assert_eq!(recipient(&from,id),"Viego"); }
            assert_eq!(recipient(&target,"viego-edge"),expected_edge);
            assert_ne!(recipient(&target,"viego-infinity"),"Jax");
            if !include_vi { assert_eq!(recipient(&target,"viego-infinity"),"Zeri"); }
            assert_eq!(recipient(&target,"viego-justice"),expected_justice);
            assert!(phases[0]["item_transfers"].as_array().unwrap().iter().all(|transfer|
                transfer["outgoing"]=="Viego" && transfer["method"]=="sell_outgoing"));
        }
        // A retained Jax still owns its items; strength cannot imply a remover.
        let held = json!([{"id":"retained","item":"Hand Of Justice","holder":"Jax"}]);
        let context = ItemContext::from_source(data,&json!({"champions":["Jax","Zeri"]}),Some(&held)).unwrap();
        let mut from = json!({"champions":["Jax","Zeri"],"planned_stars":{"Jax":2,"Zeri":2}});
        let mut target = from.clone();
        context.annotate_boards(&mut from,&mut target,&mut [],data).unwrap();
        assert_eq!(target["planned_item_builds"][0]["holder"],"Jax");
    }
}
