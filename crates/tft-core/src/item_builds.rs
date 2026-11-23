use crate::{guidance::{archetype_match,item_archetype,match_label,visit_boards,KINDS},model::GameData};
use serde_json::{json,Value};
use std::collections::HashMap;

pub fn annotate_item_builds(result:&mut Value,data:&GameData)->Result<(),String> {
    let catalog=&crate::resources::item_builds()["champions"];
    let archetypes:HashMap<_,_>=data.champions.values().map(|c|(c.name.as_str(),item_archetype(c))).collect();
    let no_ad=result["planning_context"]["no_ad_items"].as_bool().unwrap_or(false);
    let no_ap=result["planning_context"]["no_ap_items"].as_bool().unwrap_or(false);
    let builds:Vec<_>=KINDS.into_iter().filter_map(|kind|{
        if (no_ad && kind=="ad") || (no_ap && kind=="ap") || (no_ad && no_ap && kind=="flex") {return None;}
        let target=result["item_holders"][kind].as_str()?;
        let kind=if kind=="flex"&&no_ad{"ap"}else if kind=="flex"&&no_ap{"ad"}else{kind};
        let source=catalog.get(target)?;
        let items:Vec<_>=source["items"].as_array()?.iter().filter(|item|item.as_str().is_some_and(|name|crate::cap_items::item_allowed_for_plan(name,no_ad,no_ap))).cloned().collect();
        if items.is_empty() {return None;}
        let filtered=items.len()!=source["items"].as_array().unwrap().len();
        Some((kind,target.to_owned(),result["planned_stars"][target].as_u64().unwrap_or(1),archetypes.get(target).copied().unwrap_or("general"),source,items,filtered))
    }).collect();
    visit_boards(result,&mut |board|{
        let sets:Vec<_>=builds.iter().map(|(kind,target,target_stars,target_archetype,source,items,filtered)|{
            let holder=board["item_holders"][*kind].as_str()
                .or_else(||if ["ad","ap"].contains(kind){board["item_holders"]["flex"].as_str().filter(|name|data.champions.get(*name).is_some_and(|c|{let profile=crate::guidance::item_profile(c);profile==*kind || profile=="flex"}))}else{None});
            let holder_archetype=holder.and_then(|name|archetypes.get(name).copied());
            let priority=holder.zip(holder_archetype).map_or(0,|(name,archetype)|
                archetype_match(name,board["planned_stars"][name].as_u64().unwrap_or(1),archetype,target,*target_stars,target_archetype));
            let label=if *filtered {json!("Available subset of source item suggestion")}else{source["label"].clone()};
            let note=if *filtered {json!(format!("{} Unavailable offensive item categories are omitted by the NO AD items / NO AP items switches; no replacement items are invented.",source["selection_note"].as_str().unwrap_or("")))}else{source["selection_note"].clone()};
            json!({"kind":kind,"target":target,"holder":holder,"items":items,"source":source["source"],"label":label,"source_kind":source["kind"],"note":note,
                "target_archetype":target_archetype,"holder_archetype":holder_archetype,"holder_match":match_label(priority)})
        }).collect();
        board["planned_item_builds"]=json!(sets);
        Ok(())
    })
}
