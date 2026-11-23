//! Conditional post-cashout pivot. Unlock confirmations are not purchased copies.
use crate::{cap_items::ItemContext, guidance, model::{names, GameData, OptimizeOptions}, optimizer, planner};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

type Roster = BTreeSet<String>;

fn legal_groups(data:&GameData,roster:&Roster)->bool {
    let mut groups=BTreeSet::new();
    roster.iter().all(|name|data.champions[name].exclusive_group.as_ref().is_none_or(|group|groups.insert(group)))
}

fn slots(data:&GameData,roster:&Roster)->usize {
    roster.iter().map(|name|data.champions[name].slots).sum()
}

fn evaluated(data:&GameData,roster:&Roster,options:&OptimizeOptions,ranks:&BTreeMap<String,u8>)->Result<Value,String> {
    let mut board=optimizer::evaluate_team(data,&roster.iter().cloned().collect::<Vec<_>>(),&options.collab,"cap_out",&options.sona_band,ranks,Some(options.size))?;
    board["level"]=json!(options.size);
    Ok(board)
}

fn unlock_record(data:&GameData,name:&str,options:&OptimizeOptions)->Value {
    let c=&data.champions[name];
    if let Some(level)=options.completed_unlocks.get(name) {
        json!({"name":name,"condition":c.unlock_condition,"level":c.unlock_level,"source":c.unlock_source,
            "status":"completed","completed_at_level":level,
            "note":"Manually confirmed task only; buy the champion and any upgrade copies separately."})
    } else {
        json!({"name":name,"condition":c.unlock_condition,"level":c.unlock_level,"source":c.unlock_source,
            "status":"owned_copy","note":"A current copy was explicitly supplied. This does not confirm shop access or additional upgrade copies."})
    }
}

fn phase(board:Value,added:&Roster,removed:&Roster,previous:&Roster,reason:&str)->Value {
    let roster:Roster=guidance::names(&board).into_iter().collect();
    json!({"stars":board["planned_stars"],"board":board,"added":added,"removed":removed,
        "retained":previous.intersection(&roster).collect::<Vec<_>>(),"replacements":[],"item_transfers":[],
        "star_upgrades":[],"unlock_checks":[],"milestones":[reason],"reason":reason})
}
fn upgrade_checks(data:&GameData,upgrades:&[Value],options:&OptimizeOptions,conditional_source:&Roster)->Vec<Value> {
    upgrades.iter().filter_map(|upgrade| {
        let name=upgrade["champion"].as_str()?;
        let c=&data.champions[name];
        if !c.unlockable || options.completed_unlocks.get(name).is_some_and(|level|*level<=options.size) {return None;}
        Some(json!({"name":name,"condition":c.unlock_condition,"level":c.unlock_level,"source":c.unlock_source,
            "status":"required_before_upgrade","conditional_source_copy":conditional_source.contains(name),
            "note":if conditional_source.contains(name) {
                "The source copy is conditional, not confirmed owned. Complete and confirm this task before buying additional shop copies for the planned upgrade."
            } else {"The existing copy is acknowledged, but additional shop copies require completing and confirming this task first."}}))
    }).collect()
}


// Prefer one role-matched exchange at a time. Weighted units may need multiple
// incoming/outgoing bodies. An underfilled snapshot first uses its free slots.
fn exchange(data:&GameData,current:&Roster,target:&Roster,must_remove:&Roster)->Result<(Roster,Roster),String> {
    let old:Vec<_>=current.difference(target).cloned().collect();
    let new:Vec<_>=target.difference(current).cloned().collect();
    let mandatory=old.iter().enumerate().filter(|(_,n)|must_remove.contains(*n)).fold(0usize,|mask,(i,_)|mask|(1<<i));
    let mut incoming_slots=vec![0usize;1usize<<new.len()];
    for mask in 1..incoming_slots.len() {
        incoming_slots[mask]=incoming_slots[mask&(mask-1)]+data.champions[&new[mask.trailing_zeros() as usize]].slots;
    }
    let mut best:Option<(usize,i64,usize,Vec<String>,Vec<String>)>=None;
    let free=slots(data,target).saturating_sub(slots(data,current));
    for old_mask in 1usize..(1usize<<old.len()) {
        let count=old_mask.count_ones() as usize;
        if best.as_ref().is_some_and(|b|count>b.0) {continue}
        if old_mask&mandatory!=mandatory {continue}
        let removed:Vec<_>=old.iter().enumerate().filter(|(i,_)|old_mask&(1<<i)!=0).map(|(_,n)|n.clone()).collect();
        let budget:usize=removed.iter().map(|n|data.champions[n].slots).sum();
        for (new_mask,&incoming_budget) in incoming_slots.iter().enumerate().skip(1) {
            if incoming_budget!=budget+free {continue}
            let added:Vec<_>=new.iter().enumerate().filter(|(i,_)|new_mask&(1<<i)!=0).map(|(_,n)|n.clone()).collect();
            // Each original roster is exclusive-group legal. Only a retained
            // source variant versus a newly bought target can introduce a clash.
            if added.iter().any(|name|data.champions[name].exclusive_group.as_ref().is_some_and(|group|
                current.iter().filter(|n|!removed.contains(n)).any(|n|data.champions[n].exclusive_group.as_ref()==Some(group)))) {continue}
            let candidate=(count,-guidance::replacement_priority(&removed,&added,data),added.len(),removed.clone(),added);
            if best.as_ref().is_none_or(|b|candidate<*b) {best=Some(candidate)}
        }
    }
    let (_,_,_,removed,added)=best.ok_or("Cap-out cannot make a legal exact-slot exchange from this Final board.")?;
    Ok((removed.into_iter().collect(),added.into_iter().collect()))
}

pub fn recommend(data:&GameData,payload:&Value)->Result<Value,String> {
    let object=payload.as_object().ok_or("A cap-out payload must be a JSON object.")?;
    if object.keys().any(|k|!["result","completed_unlocks","current_level","item_inventory","available_stars","starting_board"].contains(&k.as_str())) {
        return Err("Unknown cap-out payload field.".into());
    }
    let result=payload.get("result").filter(|v|v.is_object()).ok_or("Cap-out requires the original planned Final recommendation.")?;
    let mut context=result.get("planning_context").filter(|v|v.is_object()).cloned()
        .ok_or("This result has no planning context; request a fresh recommendation before cap-out.")?;
    let source_mode=context["mode"].as_str().unwrap_or("high_value").to_owned();
    for key in ["completed_unlocks","current_level"] {
        if let Some(value)=payload.get(key).filter(|v|!v.is_null()) {context[key]=value.clone();}
        else if context.get(key).is_none_or(Value::is_null) {
            if let Some(value)=result.get(key).filter(|v|!v.is_null()) {context[key]=value.clone();}
        }
    }
    context["mode"]=json!("cap_out");
    context["min_collab"]=json!(0);
    context["limit"]=json!(1);
    let mut options=OptimizeOptions::from_value(&context)?;
    options.validate(data)?;
    let explicit_start=payload.get("starting_board").filter(|v|!v.is_null());
    if let Some(start)=explicit_start {
        let object=start.as_object().ok_or("starting_board must contain champions and optional stars.")?;
        if object.keys().any(|k|!["champions","stars"].contains(&k.as_str())) {return Err("Unknown starting_board field.".into());}
    }
    let source_names=names(explicit_start.map_or(&result["champions"],|v|&v["champions"]),"starting_board.champions")?;
    let source:Roster=source_names.iter().cloned().collect();
    if source.len()!=source_names.len() || source.iter().any(|n|!data.champions.contains_key(n)) {
        return Err("The starting board contains duplicate or unknown champions.".into());
    }
    if slots(data,&source)>options.size || (explicit_start.is_none() && slots(data,&source)!=options.size) || !legal_groups(data,&source) {
        return Err("The starting board must fit its slot budget without mutually exclusive variants; only an explicit starting_board may be underfilled.".into());
    }
    if explicit_start.is_none() {
        if let Some(name)=options.locked.iter().find(|name|!source.contains(*name)) {
            return Err(format!("The original Final is missing locked {name}; request a fresh recommendation before cap-out."));
        }
    }
    let available:BTreeMap<String,u8>=match payload.get("available_stars").filter(|v|!v.is_null()) {
        Some(value)=>serde_json::from_value(value.clone()).map_err(|_|"available_stars must map canonical champions to ranks 1..4.")?,
        None=>BTreeMap::new(),
    };
    if available.iter().any(|(name,rank)|!data.champions.contains_key(name)||!(1..=4).contains(rank)) {
        return Err("available_stars must map canonical champions to ranks 1..4.".into());
    }
    let mut planned_request=options.stars.clone();
    let mut source_ranks=if explicit_start.is_some() {
        data.champions.keys().map(|n|(n.clone(),1)).collect::<BTreeMap<_,_>>()
    } else {optimizer::planned_stars(data,options.size,&options.stars,&options.sona_band)?};
    let rank_value=explicit_start.and_then(|v|v.get("stars")).or_else(||if explicit_start.is_none(){result.get("planned_stars")}else{None});
    if let Some(value)=rank_value.filter(|v|!v.is_null()) {
        let ranks:BTreeMap<String,u8>=serde_json::from_value(value.clone()).map_err(|_|"Invalid starting board stars.")?;
        if ranks.iter().any(|(n,r)|!source.contains(n)||!(1..=4).contains(r)) {return Err("Invalid starting board stars.".into());}
        source_ranks.extend(ranks);
    }
    for (name,rank) in &available {
        if source.contains(name) {
            if explicit_start.and_then(|v|v["stars"].get(name)).is_some() && source_ranks[name]!=*rank {
                return Err(format!("Conflicting current ranks for {name} in starting_board and available_stars."));
            }
            source_ranks.insert(name.clone(),*rank);
        }
    }
    for name in &source {planned_request.entry(name.clone()).and_modify(|rank|*rank=(*rank).max(source_ranks[name])).or_insert(source_ranks[name]);}
    for (name,assumption) in &options.early_availability {
        if assumption.level<=options.size {
            let rank=planned_request.entry(name.clone()).or_insert(1);*rank=(*rank).max(assumption.stars);
        }
    }
    let mut final_ranks=optimizer::planned_stars(data,options.size,&planned_request,&options.sona_band)?;
    for (name,rank) in &available {final_ranks.entry(name.clone()).and_modify(|r|*r=(*r).max(*rank));}
    let collected=payload.get("item_inventory").is_some_and(|v|!v.is_null());
    let known_source=explicit_start.is_some() || collected;
    let owned:Roster=available.keys().cloned().chain(source.iter().filter(|_|known_source).cloned()).collect();
    // Every new hypothetical hit is one star. Planned guide upgrade targets
    // remain separate and are emitted only as explicit earn-and-combine phases.
    options.stars=data.champions.keys().map(|name|(name.clone(),if source.contains(name){source_ranks[name]}else{available.get(name).copied().unwrap_or(1)})).collect();
    if collected {options.no_ad_items=false;options.no_ap_items=false;}
    let mut from=optimizer::evaluate_team(data,&source_names,&options.collab,&source_mode,&options.sona_band,&source_ranks,Some(options.size))?;
    if explicit_start.is_none() {
        for key in ["lineup_order","positioning","item_holders","planned_item_builds"] {
            if let Some(value)=result.get(key) {from[key]=value.clone();}
        }
    }
    from["level"]=json!(options.size);
    if explicit_start.is_some() && !collected {
        if let Some(builds)=result["planned_item_builds"].as_array() {
            let mut builds=builds.clone();
            for build in &mut builds {
                if build["holder"].as_str().is_some_and(|name|!source.contains(name)) {
                    build["source_holder"]=build["holder"].clone();
                    build["holder"]=Value::Null;
                }
            }
            from["planned_item_builds"]=json!(builds);
        }
    }
    let items=ItemContext::from_source(data,&from,payload.get("item_inventory"))?;
    let prepared=items.prepare(data,|name|options.stars.get(name).copied().unwrap_or(1));
    let confirmed_groups:BTreeMap<_,_>=options.completed_unlocks.iter()
        .filter(|(_,level)|**level<=options.size)
        .filter_map(|(name,_)|data.champions[name].exclusive_group.as_ref().map(|g|(g.clone(),name.clone())))
        .collect();
    let mut excluded:Roster=options.excluded.iter().cloned().collect();
    let mut eligibility_reasons:BTreeMap<String,(&str,String)>=options.excluded.iter().filter(|name|source.contains(*name))
        .map(|name|(name.clone(),("explicit_exclusion","explicitly excluded by the supplied plan".to_owned()))).collect();
    let mut conditional_source=Roster::new();
    for (name,c) in &data.champions {
        let confirmed=options.completed_unlocks.get(name).is_some_and(|level|*level<=options.size);
        let published=planner::published_level(c)?;
        let shop=if (1..=5).contains(&c.cost) {planner::SHOP_ODDS[options.size-1][c.cost-1]>0} else {c.cost==7&&c.unlockable};
        let conflicting=c.exclusive_group.as_ref().and_then(|g|confirmed_groups.get(g)).is_some_and(|other|other!=name);
        if conflicting && known_source && source.contains(name) {
            return Err(format!("The starting copy of {name} conflicts with a confirmed exclusive variant. Correct the starting snapshot or unlock confirmation; ownership does not silently change either."));
        }
        // Compare a hypothetical Final against its cap under the same source
        // assumptions. This acknowledges neither ownership nor shop access.
        let assumed_source=!known_source && source.contains(name) && c.unlockable && !confirmed && !owned.contains(name);
        let eligible=(owned.contains(name) || (options.size>=published && shop && (!c.unlockable||confirmed||assumed_source))) && !conflicting && c.slots<=options.size;
        if eligible && assumed_source && !excluded.contains(name) {conditional_source.insert(name.clone());}
        if !eligible {
            if source.contains(name) || options.locked.contains(name) {
                let (code,reason)=if conflicting {("exclusive_variant","another mutually exclusive variant was confirmed".to_owned())}
                    else if c.slots>options.size {("slot_gate",format!("its slot requirement exceeds level {}",options.size))}
                    else if options.size<published {("published_minimum",format!("its published minimum level is {published}"))}
                    else if !shop {("shop_access",format!("its sourced shop odds are zero at level {}",options.size))}
                    else {("unconfirmed_unlock",format!("its sourced unlock task has not been manually confirmed by level {}",options.size))};
                if options.locked.contains(name) {
                    return Err(format!("Cap-out cannot field locked {name}: {reason}. Correct the confirmation or remove the lock; unlocked does not mean purchased."));
                }
                eligibility_reasons.entry(name.clone()).or_insert((code,reason));
            }
            excluded.insert(name.clone());
        }
    }
    options.excluded=excluded.iter().cloned().collect();
    // Pregame scenarios remain planned upgrade targets, not owned copies.
    options.early_availability.clear();
    let mut target=optimizer::optimize_with_cap_items(data,&options,&prepared)?.into_iter().next().ok_or("No legal cap-out board fills the requested Final level.")?;
    target["level"]=json!(options.size);
    let source_is_legal=slots(data,&source)==options.size && source.is_disjoint(&excluded) && options.locked.iter().all(|n|source.contains(n));
    let source_scored=optimizer::evaluate_cap_item_team(data,&source_names,&options,&prepared)?;
    from["item_fit"]=source_scored["item_fit"].clone();
    from["cap_selection_score"]=source_scored["score"].clone();
    if source_is_legal {
        if source_scored["score"].as_f64().unwrap_or(f64::NEG_INFINITY)>=target["score"].as_f64().unwrap_or(f64::NEG_INFINITY) {target=source_scored;}
    }
    target["acquisition_stars"]=target["planned_stars"].clone();
    target["cap_selection_score"]=target["score"].clone();
    target["planned_stars"]=json!(guidance::names(&target).into_iter().map(|n|{let rank=final_ranks[&n];(n,rank)}).collect::<BTreeMap<_,_>>());
    let target_roster:Roster=guidance::names(&target).into_iter().collect();
    let target_ranks:BTreeMap<String,u8>=serde_json::from_value(target["planned_stars"].clone()).map_err(|e|e.to_string())?;
    let added:Roster=target_roster.difference(&source).cloned().collect();
    let removed:Roster=source.difference(&target_roster).cloned().collect();
    let retained:Roster=source.intersection(&target_roster).cloned().collect();
    let mut current=source.clone();
    let mut ranks=source_ranks.clone();
    let mut phases=Vec::new();
    while current!=target_roster {
        // Remove hard-ineligible source targets in the first exact-slot exchange.
        // Conditional source quests remain eligible in planned comparisons.
        let mandatory:Roster=current.intersection(&excluded).cloned().collect();
        let free=options.size-slots(data,&current);
        let free_addition=target_roster.difference(&current).find(|name| {
            data.champions[*name].slots<=free && data.champions[*name].exclusive_group.as_ref().is_none_or(|group|
                !current.iter().any(|old|data.champions[old].exclusive_group.as_ref()==Some(group)))
        }).cloned();
        let (outgoing,incoming)=if let Some(name)=free_addition {(Roster::new(),[name].into_iter().collect())}
            else {exchange(data,&current,&target_roster,&mandatory)?};
        let mut following=current.clone();
        for name in &outgoing {following.remove(name);}
        for name in &incoming {following.insert(name.clone());ranks.insert(name.clone(),available.get(name).copied().unwrap_or(1));}
        let board=evaluated(data,&following,&options,&ranks)?;
        let acquisition=outgoing.is_empty();
        let mut stage=phase(board,&incoming,&outgoing,&current,if acquisition {
            "Acquire the listed additions and fill currently empty board slots before selling any existing units. Known available ranks are explicit current copies; other hits start at one star."
        } else {
            "Secure the listed additions on the bench before selling any outgoing units, then field this exact-slot exchange. Hold the previous board if you lack the hits, bench space or gold. No hypothetical purchases or upgrades are assumed."
        });
        stage["kind"]=json!(if acquisition {"acquisition"} else {"exchange"});
        stage["purchases"]=json!(incoming.iter().filter(|n|!available.contains_key(*n)).map(|n|json!({"champion":n,"stars":1,"copies":1,"location":"bench"})).collect::<Vec<_>>());
        stage["known_copies"]=json!(incoming.iter().filter_map(|n|available.get(n).map(|rank|json!({"champion":n,"stars":rank}))).collect::<Vec<_>>());
        stage["actions"]=if acquisition {json!([{"action":"acquire_if_needed","champions":incoming},{"action":"field_empty_slots","champions":incoming}])}
            else {json!([{"action":"buy_before_sell","champions":incoming.iter().filter(|n|!available.contains_key(*n)).collect::<Vec<_>>(),"location":"bench"},{"action":"sell_outgoing","champions":outgoing},{"action":"field","champions":incoming}])};
        stage["unlock_checks"]=json!(incoming.iter().filter(|n|data.champions[*n].unlockable).map(|n|unlock_record(data,n,&options)).collect::<Vec<_>>());
        if outgoing.iter().chain(&incoming).any(|n|data.champions[n].slots>1) {
            stage["milestones"].as_array_mut().unwrap().push(json!("Weighted-slot exchange: secure every listed incoming unit before freeing the complete outgoing slot group."));
        }
        if !mandatory.is_empty() {
            stage["milestones"].as_array_mut().unwrap().push(json!("Hard-ineligible or explicitly excluded source targets are removed together here. Conditional source quest eligibility alone does not require a sale."));
        }
        phases.push(stage);
        current=following;
        let mut upgrades=Vec::new();
        for name in &incoming {
            let from_rank=ranks[name];
            let to=target_ranks[name];
            if to>from_rank {
                ranks.insert(name.clone(),to);
                upgrades.push(json!({"champion":name,"from":from_rank,"to":to,"additional_copies":3usize.pow((to-1) as u32)-3usize.pow((from_rank-1) as u32)}));
            }
        }
        if !upgrades.is_empty() {
            let board=evaluated(data,&current,&options,&ranks)?;
            let mut stage=phase(board,&Roster::new(),&Roster::new(),&current,
                "Complete any listed shop-access tasks, then buy and combine the required additional copies before counting these planned star upgrades. Until then use the preceding board; no upgrade, copies or gold are assumed owned.");
            stage["kind"]=json!("star_upgrade");
            stage["unlock_checks"]=json!(upgrade_checks(data,&upgrades,&options,&conditional_source));
            stage["star_upgrades"]=json!(upgrades);
            phases.push(stage);
        }
    }
    // Scenario targets can also raise a retained unit's rank. Buy and combine
    // those copies explicitly instead of silently changing the source board.
    let mut upgrades=Vec::new();
    for name in &retained {
        let from=ranks[name];
        let to=target_ranks[name];
        if to>from {
            upgrades.push(json!({"champion":name,"from":from,"to":to,"additional_copies":3usize.pow((to-1) as u32)-3usize.pow((from-1) as u32)}));
            ranks.insert(name.clone(),to);
        }
    }
    if !upgrades.is_empty() {
        let board=evaluated(data,&current,&options,&ranks)?;
        let mut stage=phase(board,&Roster::new(),&Roster::new(),&current,
            "Complete any listed shop-access tasks, then buy and combine the required additional copies before counting these planned star upgrades. Until then use the preceding board; no upgrade, copies or gold are assumed owned.");
        stage["kind"]=json!("star_upgrade");
        stage["unlock_checks"]=json!(upgrade_checks(data,&upgrades,&options,&conditional_source));
        stage["star_upgrades"]=json!(upgrades);
        phases.push(stage);
    }
    guidance::annotate_cap_transition(&mut from,&mut target,&mut phases,data,&items)?;
    let overhaul_note="Multiple champions change together in this cap-out step; stage the complete incoming group before starting.";
    let mut has_overhaul=false;
    for stage in &mut phases {
        if stage["removed"].as_array().is_some_and(|names|names.len()>=3) || stage["added"].as_array().is_some_and(|names|names.len()>=3) {
            stage["pivot_warning"]=json!({"level":options.size,"added":stage["added"],"removed":stage["removed"],"reason":overhaul_note});
            has_overhaul=true;
        }
    }
    let replacements:Vec<_>=phases.iter().flat_map(|p|p["replacements"].as_array().into_iter().flatten().cloned()).collect();
    let item_transfers:Vec<_>=phases.iter().flat_map(|p|p["item_transfers"].as_array().into_iter().flatten().cloned())
        .chain(if phases.is_empty(){target["item_transfers"].as_array().cloned().unwrap_or_default()}else{Vec::new()}).collect();
    let mut notes=vec![
        "Cap-out is a post-cashout planning scenario, not a claim that Sona's unlock or the Collab cashout happened. Collab has no minimum here; Sona and cheap fillers may be replaced unless explicitly locked.".to_owned(),
        "Selection uses source ranks under the displayed source assumptions and explicitly available copies; new hypothetical hits are one star. Eventual guide upgrades remain conditional targets and require the displayed additional copies.".to_owned(),
        "Unlock checkmarks only confirm tasks. Every addition, upgrade and item transfer remains conditional on actual purchases, copies and items; retained holders need a remover or later sale.".to_owned(),
        format!("Final level remains {}. Published minimums, sourced shop odds, exclusions, mutually exclusive variants and slot requirements remain hard gates. New quest champions require a manual confirmation by that level unless an actual copy was explicitly supplied. Shop source: {}",options.size,planner::SHOP_SOURCE),
    ];
    if has_overhaul { notes.push(overhaul_note.into()); }
    notes.extend(items.notes());
    if explicit_start.is_none() {notes.push("Starting roster and source stars are the original selected plan, not confirmed in-game ownership. Supply starting_board to compare an exact current snapshot.".into());}
    if !conditional_source.is_empty() {notes.push("Planned Final quest champions remain conditional eligible source copies in both sides of this what-if comparison, not confirmed unlocks, ownership, purchases or cashout. Additional shop copies still require completing and confirming their tasks.".into());}
    if collected && (context["no_ad_items"]==true || context["no_ap_items"]==true) {notes.push("Collected inventory takes precedence over NO AD items / NO AP items hypothetical guide switches; no actual item is removed or replaced.".into());}
    if phases.is_empty() {
        if target["item_transfers"].as_array().is_none_or(|transfers|transfers.is_empty()) {target=from.clone();}
        notes.push("The starting board is already the best legal cap-out candidate under these items, locks, exclusions, source assumptions, ranks and confirmations; no exchanges are recommended.".into());
    }
    let preserved_core:Vec<_>=retained.iter().filter(|name|source_ranks[*name]>=2 && data.champions[*name].cost>=4).cloned().collect();
    let source_unlock_assumptions:Vec<_>=conditional_source.iter().map(|name| {
        let c=&data.champions[name];
        json!({"name":name,"condition":c.unlock_condition,"level":c.unlock_level,"source":c.unlock_source,
            "status":"conditional_source_copy","stars":source_ranks[name],"retained":retained.contains(name),
            "confirms_unlock":false,"confirms_ownership":false,"confirms_additional_copies":false,"confirms_purchases":false,"confirms_cashout":false,
            "note":"Conditional source Final copy only, shared by both compared boards. Complete the task and acquire the source copies in play; additional shop copies require a confirmed task."})
    }).collect();
    let replacement_details:Vec<_>=removed.iter().map(|name| {
        let (kind,code,reason)=if let Some((code,reason))=eligibility_reasons.get(name) {
            ("eligibility",*code,format!("{name}: not eligible because {reason}."))
        } else {
            let quest_note=if conditional_source.contains(name) {" Its source quest remains conditionally eligible; an unchecked task is not the reason for this replacement."} else {""};
            ("heuristic","heuristic_selection",format!("{name}: replaced by the finite-item and current-rank combat/support heuristic under the same source assumptions.{quest_note} Acquire every listed replacement before selling."))
        };
        json!({"name":name,"kind":kind,"code":code,"reason":reason,"conditional_source_unlock":conditional_source.contains(name)})
    }).collect();
    let replacement_reasons:Vec<_>=replacement_details.iter().map(|detail|detail["reason"].clone()).collect();
    let mut response=json!({"board":target,"from_board":from,"phases":phases,"added":added,"removed":removed,"retained":retained,
        "replacements":replacements,"item_transfers":item_transfers,"notes":notes,"preserved_core":preserved_core,"replacement_reasons":replacement_reasons,
        "replacement_details":replacement_details,"source_unlock_assumptions":source_unlock_assumptions,
        "comparison_mode":if known_source {"actual"} else {"planned_final"},
        "selection_comparison":{"source_eligible":source_is_legal,"source_score":from["cap_selection_score"],
            "selected_score":target["cap_selection_score"],"same_source_unlock_assumptions":true},
        "original_final":{"champions":result["champions"],"planned_stars":result["planned_stars"]},
        "available_stars":available,"starting_board":explicit_start,"item_fit":target["item_fit"],
        "level":options.size,"completed_unlocks":options.completed_unlocks,"current_level":options.current_level});
    if let Some(metadata)=items.metadata().as_object() {response.as_object_mut().unwrap().extend(metadata.clone());}
    response["item_bench"]=response["board"]["item_bench"].clone();
    Ok(response)
}
