use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use tft_core::{cap_out, dispatch_with_data, guidance, model::GameData, optimizer, resources};

fn roster(board:&Value)->BTreeSet<String> {
    board["champions"].as_array().unwrap().iter().map(|n|n.as_str().unwrap().to_owned()).collect()
}
fn source(data:&GameData,names:&[&str],size:usize,stars:Value)->Value {
    let ranks:BTreeMap<String,u8>=serde_json::from_value(stars.clone()).unwrap();
    let mut board=optimizer::evaluate_team(data,&names.iter().map(|n|(*n).to_owned()).collect::<Vec<_>>(),&[],"balanced","",&ranks,Some(size)).unwrap();
    board["level"]=json!(size);
    guidance::annotate_result(&mut board,data).unwrap();
    board["planning_context"]=json!({"size":size,"mode":"balanced","min_collab":0,"collab":[],"locked":[],"excluded":[],"stars":stars});
    board
}
fn pool()->GameData {
    GameData::from_value(json!({"traits":[],"champions":[
        {"name":"Anchor","cost":4,"traits":[],"role":"Magic Tank"},
        {"name":"Filler","cost":1,"traits":[],"role":"Attack Specialist"},
        {"name":"Caster","cost":4,"traits":[],"role":"Magic Caster"},
        {"name":"Ranged","cost":4,"traits":[],"role":"Attack Marksman"},
        {"name":"Melee","cost":4,"traits":[],"role":"Attack Fighter"}
    ]})).unwrap()
}
fn inventory(items:&[&str],holder:Option<&str>)->Value {
    json!(items.iter().enumerate().map(|(i,item)|json!({"id":format!("item-{i}"),"item":item,"holder":holder})).collect::<Vec<_>>())
}
fn assert_budget(out:&Value,expected:&Value) {
    let expected:BTreeSet<_>=expected.as_array().unwrap().iter().map(|v|v["id"].as_str().unwrap()).collect();
    for board in std::iter::once(&out["from_board"]).chain(std::iter::once(&out["board"]))
        .chain(out["phases"].as_array().unwrap().iter().map(|p|&p["board"])) {
        let mut ids=BTreeSet::new();
        for build in board["planned_item_builds"].as_array().unwrap() {
            assert!(build["item_ids"].as_array().unwrap().len()<=3);
            for id in build["item_ids"].as_array().unwrap() {assert!(ids.insert(id.as_str().unwrap()));}
        }
        for item in board["item_bench"].as_array().unwrap() {assert!(ids.insert(item["id"].as_str().unwrap()));}
        assert_eq!(ids,expected);
    }
}
#[test]
fn same_pool_and_known_ranks_choose_distinct_typed_item_users() {
    let d=pool();
    let result=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2,"Filler":1}));
    let available=json!({"Anchor":2,"Filler":1,"Caster":1,"Ranged":1,"Melee":1});
    for (items,wanted) in [
        (["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],"Caster"),
        (["Guinsoo's Rageblade","Infinity Edge","Kraken's Fury"],"Ranged"),
        (["Bloodthirster","Titan's Resolve","Sterak's Gage"],"Melee")
    ] {
        let items=inventory(&items,None);
        let payload=json!({"result":result,"item_inventory":items,"available_stars":available,
            "starting_board":{"champions":["Anchor","Filler"],"stars":{"Anchor":2,"Filler":1}}});
        let original=payload.clone();
        let out=dispatch_with_data(&d,"cap_out",&payload).unwrap();
        assert!(roster(&out["board"]).contains(wanted),"{wanted}: {}",out["board"]);
        assert!(roster(&out["board"]).contains("Anchor"));
        assert_eq!(out["item_mode"],"collected");
        assert_budget(&out,&items);
        assert_eq!(payload,original);
    }
}
#[test]
fn exact_underfilled_snapshot_fills_free_slots_before_selling() {
    let d=pool();
    let result=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2}));
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],None);
    let out=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,
        "starting_board":{"champions":["Anchor"],"stars":{"Anchor":2}},"available_stars":{"Caster":1}})).unwrap();
    assert_eq!(roster(&out["from_board"]),BTreeSet::from(["Anchor".to_owned()]));
    let first=&out["phases"][0];
    assert_eq!(first["kind"],"acquisition");
    assert!(first["removed"].as_array().unwrap().is_empty());
    assert_eq!(roster(&first["board"]).len(),2);
    assert_budget(&out,&items);
}
#[test]
fn actual_inventory_overrides_hypothetical_damage_switches() {
    let d=pool();
    let mut result=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2}));
    result["planning_context"]["no_ap_items"]=json!(true);
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],None);
    let out=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"available_stars":{"Caster":1,"Melee":1,"Ranged":1}})).unwrap();
    assert!(roster(&out["board"]).contains("Caster"));
    assert_budget(&out,&items);
    let empty=cap_out::recommend(&d,&json!({"result":result,"item_inventory":[],"available_stars":{"Caster":1}})).unwrap();
    assert!(empty["item_budget"].as_array().unwrap().is_empty());
    assert!(empty["board"]["planned_item_builds"].as_array().unwrap().is_empty());
}
#[test]
fn upgraded_itemized_manager_core_is_not_sold_for_unupgraded_expensive_units() {
    let d=resources::bundled_data();
    let names=["Alune","Leona","Elise","Gnar","Katarina","Kayn","Kennen","Lillia","Neeko","Olaf"];
    let result=source(d,&names,10,json!({"Alune":2,"Leona":2}));
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],Some("Alune"));
    let ranks:Value=json!(names.iter().map(|name|((*name).to_owned(),if ["Alune","Leona"].contains(name){2}else{1})).collect::<BTreeMap<_,_>>());
    let payload=json!({"result":result,"item_inventory":items,"starting_board":{"champions":names,"stars":ranks},"current_level":10});
    let out=cap_out::recommend(d,&payload).unwrap();
    assert!(roster(&out["board"]).contains("Alune"));
    assert!(roster(&out["board"]).contains("Leona"));
    assert_eq!(out["completed_unlocks"],json!({}));
    assert_eq!(out["from_board"]["planned_stars"]["Alune"],2);
    assert_budget(&out,&items);
}
#[test]
fn normal_damage_alternatives_preserve_context_and_omit_forbidden_plans() {
    let data=GameData::from_value(json!({"traits":[],"champions":[
        {"name":"Ahri","cost":1,"traits":[],"role":"Magic Caster"},
        {"name":"Aphelios","cost":1,"traits":[],"role":"Attack Marksman"}
    ]})).unwrap();
    for (no_ad,no_ap,wanted) in [(true,false,"Ahri"),(false,true,"Aphelios")] {
        let out=dispatch_with_data(&data,"recommend",&json!({"size":1,"min_collab":0,"limit":1,"no_ad_items":no_ad,"no_ap_items":no_ap})).unwrap();
        let result=&out[0];
        assert!(roster(result).contains(wanted));
        assert_eq!(result["planning_context"]["no_ad_items"],no_ad);
        assert_eq!(result["planning_context"]["no_ap_items"],no_ap);
        for build in result["planned_item_builds"].as_array().unwrap() {
            assert_ne!(build["kind"],if no_ad {"ad"} else {"ap"});
        }
        let replanned=dispatch_with_data(&data,"replan",&json!({"result":result,"temporary_choices":{}})).unwrap();
        assert_eq!(replanned["planning_context"],result["planning_context"]);
    }
    let both=dispatch_with_data(&data,"recommend",&json!({"size":1,"min_collab":0,"limit":1,"no_ad_items":true,"no_ap_items":true})).unwrap();
    assert!(both[0]["planned_item_builds"].as_array().unwrap().is_empty());
    let error=dispatch_with_data(&data,"recommend",&json!({"size":1,"min_collab":0,"locked":["Aphelios"],"no_ad_items":true})).unwrap_err();
    assert!(error.contains("carry")&&error.contains("locked"));
}

#[test]
fn different_original_source_plans_change_search_and_actual_items_override_them() {
    let d=pool();
    let base=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2,"Filler":1}));
    let available=json!({"Anchor":2,"Caster":1,"Melee":1,"Ranged":1});
    let mage=["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"];
    for (trio,wanted,kind) in [(mage,"Caster","ap"),(["Bloodthirster","Titan's Resolve","Sterak's Gage"],"Melee","ad")] {
        let mut result=base.clone();
        result["planned_item_builds"]=json!([{"kind":kind,"target":"Filler","holder":"Filler","items":trio,
            "source":"https://example.test/source-plan","source_kind":"role_based","label":"Original plan","note":"Finite selected what-if."}]);
        let out=cap_out::recommend(&d,&json!({"result":result,"available_stars":available})).unwrap();
        assert_eq!(out["item_mode"],"planned");
        assert!(roster(&out["board"]).contains(wanted));
        assert_eq!(out["item_budget"].as_array().unwrap().len(),3);
        let actual=inventory(&mage,None);
        let out=cap_out::recommend(&d,&json!({"result":result,"available_stars":available,"item_inventory":actual})).unwrap();
        assert!(roster(&out["board"]).contains("Caster"));
        assert_budget(&out,&actual);
    }
}

#[test]
fn known_replacement_rank_can_justify_a_real_pivot_without_assuming_guide_upgrades() {
    let mut raw=pool().raw;
    raw["champions"].as_array_mut().unwrap().push(json!({"name":"Legendary","cost":5,"traits":[],"role":"Magic Caster"}));
    let d=GameData::from_value(raw).unwrap();
    let result=source(&d,&["Anchor","Caster"],2,json!({"Anchor":2,"Caster":2}));
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],Some("Caster"));
    let start=json!({"champions":["Anchor","Caster"],"stars":{"Anchor":2,"Caster":2}});
    let one=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"starting_board":start,"available_stars":{"Legendary":1}})).unwrap();
    assert!(roster(&one["board"]).contains("Caster"));
    assert!(!roster(&one["board"]).contains("Legendary"));
    let upgraded=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"starting_board":start,"available_stars":{"Legendary":3}})).unwrap();
    assert!(roster(&upgraded["board"]).contains("Legendary"));
    assert_eq!(upgraded["board"]["acquisition_stars"]["Legendary"],3);
    assert_budget(&upgraded,&items);
}

#[test]
fn known_quest_copy_does_not_claim_the_task_or_new_shop_copies() {
    let mut raw=pool().raw;
    raw["champions"].as_array_mut().unwrap().push(json!({"name":"Quest Caster","cost":5,"traits":[],"role":"Magic Caster",
        "unlockable":true,"unlock_level":"Level 2","unlock_condition":"Complete the sourced task.","unlock_source":"https://example.test/task"}));
    let d=GameData::from_value(raw).unwrap();
    let mut result=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2}));
    result["planning_context"]["locked"]=json!(["Quest Caster"]);
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],None);
    let start=json!({"champions":["Anchor"],"stars":{"Anchor":2}});
    let out=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"starting_board":start,"available_stars":{"Quest Caster":1}})).unwrap();
    assert!(roster(&out["board"]).contains("Quest Caster"));
    assert_eq!(out["completed_unlocks"],json!({}));
    assert_eq!(out["phases"][0]["unlock_checks"][0]["status"],"owned_copy");
    let error=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"starting_board":start})).unwrap_err();
    assert!(error.contains("locked Quest Caster")&&error.contains("sourced shop odds are zero at level 2"));
    let confirmed=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items,"starting_board":start,
        "completed_unlocks":{"Quest Caster":2}})).unwrap_err();
    assert!(confirmed.contains("locked Quest Caster")&&confirmed.contains("sourced shop odds are zero at level 2"));
}

#[test]
fn planned_source_quest_upgrades_remain_conditional_and_do_not_unlock_new_quests() {
    let mut raw=pool().raw;
    for name in ["Quest Caster","New Quest"] {
        raw["champions"].as_array_mut().unwrap().push(json!({"name":name,"cost":7,"traits":[],"role":"Magic Caster",
            "unlockable":true,"unlock_level":"Level 2","unlock_condition":"Complete the sourced task.","unlock_source":"https://example.test/task"}));
    }
    let d=GameData::from_value(raw).unwrap();
    let mut result=source(&d,&["Anchor","Quest Caster"],2,json!({"Anchor":2,"Quest Caster":1}));
    result["planning_context"]["locked"]=json!(["Anchor","Quest Caster"]);
    result["planning_context"]["early_availability"]=json!({"Quest Caster":{"level":2,"stars":2}});
    result["planned_item_builds"]=json!([{"kind":"ap","target":"Quest Caster","holder":"Quest Caster",
        "items":["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"]}]);
    let out=cap_out::recommend(&d,&json!({"result":result,"available_stars":{"Anchor":2}})).unwrap();
    assert_eq!(out["completed_unlocks"],json!({}));
    assert_eq!(out["available_stars"],json!({"Anchor":2}));
    assert_eq!(out["source_unlock_assumptions"][0]["name"],"Quest Caster");
    assert_eq!(out["source_unlock_assumptions"][0]["confirms_purchases"],false);
    assert_eq!(out["source_unlock_assumptions"][0]["confirms_cashout"],false);
    assert_eq!(out["source_unlock_assumptions"][0]["confirms_additional_copies"],false);
    let upgrade=out["phases"].as_array().unwrap().iter().find(|phase|phase["kind"]=="star_upgrade").unwrap();
    assert_eq!(upgrade["star_upgrades"][0]["champion"],"Quest Caster");
    assert_eq!(upgrade["star_upgrades"][0]["additional_copies"],2);
    assert_eq!(upgrade["unlock_checks"][0]["status"],"required_before_upgrade");
    assert_eq!(upgrade["unlock_checks"][0]["conditional_source_copy"],true);
    assert!(upgrade["unlock_checks"][0]["note"].as_str().unwrap().contains("not confirmed owned"));
    assert_eq!(out["board"]["acquisition_stars"]["Quest Caster"],1);
    assert!(!roster(&out["board"]).contains("New Quest"));
    assert_budget(&out,&out["item_budget"]);
    let mut new_locked=result.clone();
    new_locked["planning_context"]["locked"]=json!(["New Quest"]);
    let error=cap_out::recommend(&d,&json!({"result":new_locked,
        "starting_board":{"champions":["Anchor","Quest Caster"]}})).unwrap_err();
    assert!(error.contains("locked New Quest")&&error.contains("confirmed"));
}

#[test]
fn actual_snapshot_does_not_inherit_planned_source_quest_eligibility() {
    let mut raw=pool().raw;
    raw["champions"].as_array_mut().unwrap().push(json!({"name":"Quest Caster","cost":7,"traits":[],"role":"Magic Caster",
        "unlockable":true,"unlock_level":"Level 2","unlock_condition":"Complete the sourced task.","unlock_source":"https://example.test/task"}));
    let d=GameData::from_value(raw).unwrap();
    let result=source(&d,&["Anchor","Quest Caster"],2,json!({"Anchor":2,"Quest Caster":2}));
    let alternative=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2,"Filler":1}));
    let start=json!({"champions":["Anchor","Filler"],"stars":{"Anchor":2,"Filler":1}});
    let items=inventory(&["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],None);
    for inventory in [Value::Null,items.clone()] {
        let actual=cap_out::recommend(&d,&json!({"result":result,"starting_board":start,"item_inventory":inventory})).unwrap();
        let other=cap_out::recommend(&d,&json!({"result":alternative,"starting_board":start,"item_inventory":inventory})).unwrap();
        assert_eq!(actual["comparison_mode"],"actual");
        assert!(actual["source_unlock_assumptions"].as_array().unwrap().is_empty());
        assert!(!roster(&actual["board"]).contains("Quest Caster"));
        assert_eq!(roster(&actual["board"]),roster(&other["board"]));
        assert_eq!(actual["completed_unlocks"],json!({}));
        if !inventory.is_null() {assert_budget(&actual,&inventory);}
    }
    let collected=cap_out::recommend(&d,&json!({"result":result,"item_inventory":items})).unwrap();
    assert_eq!(collected["comparison_mode"],"actual");
    assert!(collected["source_unlock_assumptions"].as_array().unwrap().is_empty());
    assert_eq!(collected["completed_unlocks"],json!({}));
    assert!(roster(&collected["board"]).contains("Quest Caster"));
    assert_budget(&collected,&items);
}

#[test]
fn starting_snapshot_validation_is_strict_and_read_only() {
    let d=pool();
    let result=source(&d,&["Anchor","Filler"],2,json!({"Anchor":2}));
    for start in [json!({"champions":["Anchor","Anchor"]}),json!({"champions":["Missing"]}),
        json!({"champions":["Anchor"],"stars":{"Filler":2}}),json!({"champions":["Anchor"],"stars":{"Anchor":0}}),
        json!({"champions":["Anchor","Filler","Caster"]}),json!({"champions":["Anchor"],"owned":true})] {
        let payload=json!({"result":result,"starting_board":start,"item_inventory":[]});
        let original=payload.clone();
        assert!(cap_out::recommend(&d,&payload).is_err());
        assert_eq!(payload,original);
    }
    assert!(cap_out::recommend(&d,&json!({"result":result,"starting_board":{"champions":["Anchor"]},
        "item_inventory":inventory(&["Spear of Shojin"],Some("Filler"))})).is_err());
}
