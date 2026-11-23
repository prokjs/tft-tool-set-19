use serde_json::{json, Value};
use std::collections::BTreeSet;
use tft_core::{dispatch_with_data, guidance, model::{GameData, OptimizeOptions}, planner, resources};

fn names(board:&Value)->BTreeSet<String>{
    board["champions"].as_array().unwrap().iter().map(|name|name.as_str().unwrap().to_owned()).collect()
}
fn recommend(data:&GameData,options:Value)->Value{
    planner::recommend_with_buildups(data,&OptimizeOptions::from_value(&options).unwrap()).unwrap().remove(0)
}
fn alternate(data:&GameData,result:&Value,level:Value,phase:usize,no_ad:bool,no_ap:bool)->Result<Value,String>{
    dispatch_with_data(data,"damage_alternative",&json!({"result":result,"level":level,"phase":phase,"no_ad_items":no_ad,"no_ap_items":no_ap}))
}
fn final_data()->GameData{
    GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2],"unique":false}],"champions":[
        {"name":"Anchor","cost":1,"traits":["Band"],"role":"Attack Tank"},
        {"name":"Core","cost":1,"traits":["Band"],"role":"Magic Tank"},
        {"name":"Aphelios","cost":1,"traits":["Band"],"role":"Attack Marksman"},
        {"name":"Ahri","cost":1,"traits":["Band"],"role":"Magic Caster"},
        {"name":"Premium","cost":3,"traits":["Band"],"role":"Magic Tank"},
        {"name":"Yone","cost":1,"traits":["Band"],"role":"Attack Fighter"},
        {"name":"Gated","cost":5,"traits":["Band"],"role":"Magic Caster"}
    ]})).unwrap()
}
fn final_source(data:&GameData)->Value{
    let mut result=recommend(data,json!({"size":3,"locked":["Anchor","Core","Aphelios"],"min_collab":0,"limit":1,"stars":{"Anchor":3,"Core":2,"Aphelios":2}}));
    result["planning_context"]["locked"]=json!(["Anchor"]);
    result["planning_context"]["excluded"]=json!(["Yone"]);
    result
}
#[test]
fn final_alternative_replaces_only_incompatible_carry_and_preserves_context(){
    let data=final_data();let source=final_source(&data);let original=source.clone();
    let result=alternate(&data,&source,Value::Null,0,true,false).unwrap();
    assert_eq!(names(&result),BTreeSet::from(["Anchor".into(),"Core".into(),"Ahri".into()]));
    assert_eq!(result["planning_context"]["locked"],source["planning_context"]["locked"]);
    assert_eq!(result["planning_context"]["excluded"],source["planning_context"]["excluded"]);
    assert_eq!(result["planning_context"]["min_collab"],0);
    assert_eq!(result["planned_stars"]["Anchor"],source["planned_stars"]["Anchor"]);
    assert_eq!(result["planned_stars"]["Core"],source["planned_stars"]["Core"]);
    assert_eq!(result["planning_context"]["no_ad_items"],true);
    assert!(result["planned_item_builds"].as_array().unwrap().iter().all(|build|build["kind"]!="ad"));
    assert_eq!(source,original);
}
#[test]
fn final_locks_and_real_shop_gates_are_not_silently_relaxed(){
    let data=final_data();let mut source=final_source(&data);
    source["planning_context"]["locked"]=json!(["Anchor","Aphelios"]);
    assert!(alternate(&data,&source,Value::Null,0,true,false).unwrap_err().contains("locked"));
    source["planning_context"]["locked"]=json!(["Anchor"]);
    source["planning_context"]["excluded"]=json!(["Ahri","Yone"]);
    assert!(alternate(&data,&source,Value::Null,0,true,false).is_err());
    let result=alternate(&data,&source,Value::Null,0,true,true).unwrap();
    assert_eq!(result["champions"],source["champions"]);
    assert!(result["planned_item_builds"].as_array().unwrap().iter().all(|build|build["kind"]=="tank"));
}
fn buildup_data()->GameData{
    let mut champions=Vec::new();
    for i in 0..9{champions.push(json!({"name":format!("Attack{i:02}"),"cost":1,"traits":["Band"],"role":"Attack Marksman"}));}
    for i in 0..4{champions.push(json!({"name":format!("Magic{i:02}"),"cost":1,"traits":["Band"],"role":"Magic Caster"}));}
    for i in 0..7{champions.push(json!({"name":format!("Goal{i}"),"cost":4,"traits":["Band"],"role":"Attack Tank"}));}
    champions.push(json!({"name":"Confirmed","cost":1,"traits":["Band"],"role":"Attack Tank","unlockable":true,"unlock_level":"Any","unlock_condition":"Win a combat.","unlock_source":"https://example.test/unlock"}));
    GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2,4],"unique":false}],"champions":champions})).unwrap()
}
fn buildup_source(data:&GameData)->Value{
    let mut result=recommend(data,json!({"size":7,"min_collab":0,"limit":1,"locked":(0..7).map(|i|format!("Goal{i}")).collect::<Vec<_>>(),
        "excluded":["Magic00","Magic01","Magic02","Magic03"],"current_level":4,"completed_unlocks":{"Confirmed":3},"stars":{"Attack08":1,"Goal0":2},
        "early_availability":{"Attack00":{"level":4,"stars":3},"Goal6":{"level":6,"stars":1}}}));
    result["planning_context"]["excluded"]=json!([]);
    result
}
fn phase_board(result:&Value,level:usize,phase:usize)->&Value{
    if phase==0{&result["buildup"][level-1]["board"]}else{&result["buildup"][level-1]["upgrades"][phase-1]["board"]}
}
#[test]
fn displayed_upgrade_alternative_preserves_history_final_gates_and_retained_ranks(){
    let data=buildup_data();let source=buildup_source(&data);let original=source.clone();
    let phase=source["buildup"][3]["upgrades"].as_array().unwrap().len();
    assert!(phase>0,"Level four must expose an earned star phase.");
    let displayed=phase_board(&source,4,phase);
    let result=alternate(&data,&source,json!(4),phase,true,false).unwrap();
    assert_eq!(result["champions"],source["champions"]);
    assert_eq!(result["planned_stars"],source["planned_stars"]);
    assert_eq!(result["planning_context"],source["planning_context"]);
    assert_eq!(result["completed_unlocks"],source["completed_unlocks"]);
    assert_eq!(result["early_availability"],source["early_availability"]);
    for level in 0..3{assert_eq!(result["buildup"][level],source["buildup"][level]);}
    for index in 0..phase{assert_eq!(result["buildup"][3]["upgrades"][index],source["buildup"][3]["upgrades"][index]);}
    let selected=result["damage_alternative"]["phase"].as_u64().unwrap() as usize;
    assert!(selected>phase);
    let board=phase_board(&result,4,selected);let before=names(displayed);let after=names(board);
    assert_eq!(before.difference(&after).count(),1);
    assert_eq!(after.difference(&before).count(),1);
    assert!(after.difference(&before).all(|name|guidance::item_profile(&data.champions[name])=="ap"));
    for name in before.intersection(&after){assert_eq!(board["planned_stars"][name],displayed["planned_stars"][name]);}
    for name in after.difference(&before){assert_eq!(board["planned_stars"][name],1);}
    assert_eq!(result["buildup"][6]["goal_board"],source["buildup"][6]["goal_board"]);
    assert_eq!(source,original);
}
#[test]
fn arrival_and_both_flags_do_not_erase_history_or_invent_inventory(){
    let data=buildup_data();let mut source=buildup_source(&data);
    source["item_inventory"]=json!([{"id":"actual","name":"Jeweled Gauntlet"}]);
    source["starting_board"]=json!({"champions":["Attack00"],"stars":{"Attack00":3}});
    let result=alternate(&data,&source,json!(4),0,true,false).unwrap();
    assert!(result["damage_alternative"]["phase"].as_u64().unwrap()>0);
    assert_eq!(result["item_inventory"],source["item_inventory"]);
    assert_eq!(result["starting_board"],source["starting_board"]);
    let both=alternate(&data,&source,json!(4),0,true,true).unwrap();
    assert_eq!(both["champions"],source["champions"]);
    assert_eq!(both["buildup"][3]["board"]["champions"],source["buildup"][3]["board"]["champions"]);
    assert_eq!(both["buildup"][2],source["buildup"][2]);
    assert_eq!(both["item_inventory"],source["item_inventory"]);
}
#[test]
fn displayed_exact_final_phase_uses_minimal_final_alternative(){
    let data=final_data();let source=final_source(&data);
    let phase=source["buildup"][2]["upgrades"].as_array().unwrap().len();
    let result=alternate(&data,&source,json!(3),phase,true,false).unwrap();
    assert_eq!(names(&result),BTreeSet::from(["Anchor".into(),"Core".into(),"Ahri".into()]));
    for level in 0..2{assert_eq!(result["buildup"][level],source["buildup"][level]);}
    assert_eq!(result["damage_alternative"]["level"],3);
}
#[test]
fn flex_items_keep_true_source_and_never_invent_a_missing_magic_trio(){
    let data=final_data();let source=recommend(&data,json!({"size":1,"locked":["Yone"],"min_collab":0,"limit":1}));
    let no_ap=alternate(&data,&source,Value::Null,0,false,true).unwrap();
    let builds=no_ap["planned_item_builds"].as_array().unwrap();
    assert!(builds.iter().any(|build|build["kind"]=="ad"&&build["target"]=="Yone"));
    for build in builds{assert_eq!(build["source"],resources::item_builds()["champions"]["Yone"]["source"]);}
    let no_ad=alternate(&data,&source,Value::Null,0,true,false).unwrap();
    assert_eq!(names(&no_ad),names(&source));
    assert!(no_ad["planned_item_builds"].as_array().unwrap().is_empty());
    let both=alternate(&data,&source,Value::Null,0,true,true).unwrap();
    assert!(both["planned_item_builds"].as_array().unwrap().is_empty());
}
#[test]
fn malformed_or_unsupported_damage_requests_fail_honestly(){
    let data=final_data();let source=final_source(&data);
    for (key,value) in [("no_ad_items",json!("yes")),("no_ap_items",Value::Null),("phase",json!(-1)),("phase",json!(1.5)),("level",json!(0)),("level",json!("4")),("unexpected",json!(true))]{
        let mut payload=json!({"result":source,"level":null,"phase":0,"no_ad_items":true,"no_ap_items":false});payload[key]=value;
        assert!(dispatch_with_data(&data,"damage_alternative",&payload).is_err(),"{payload}");
    }
    assert!(alternate(&data,&source,json!(4),0,true,false).is_err());
    assert!(alternate(&data,&source,json!(2),999,true,false).is_err());
}

#[test]
fn future_pins_survive_the_phase_local_continuation(){
    let data=buildup_data();let baseline=buildup_source(&data);
    let option=baseline["buildup"][4]["temporary_options"].as_array().unwrap().iter().find(|option|
        option["selected"]!="Attack00"&&option["options"].as_array().unwrap().iter().filter_map(Value::as_str).any(|name|
            name.starts_with("Attack")&&Some(name)!=option["selected"].as_str())).unwrap();
    let slot=option["slot"].as_str().unwrap();
    let replacement=option["options"].as_array().unwrap().iter().filter_map(Value::as_str).find(|name|
        name.starts_with("Attack")&&Some(*name)!=option["selected"].as_str()).unwrap();
    let choices=json!({"5":{(slot):replacement}});
    let source=planner::replan_buildup(&data,&baseline,Some(&choices),None,None,None).unwrap();
    let phase=source["buildup"][3]["upgrades"].as_array().unwrap().len();
    let result=alternate(&data,&source,json!(4),phase,true,false).unwrap();
    assert_eq!(result["temporary_choices"]["5"],choices["5"]);
    assert!(names(&result["buildup"][4]["board"]).contains(replacement));
    assert_eq!(result["buildup"][2],source["buildup"][2]);
    assert_eq!(result["champions"],source["champions"]);
}

#[test]
fn final_item_alternative_keeps_manual_canonical_slot_after_first_swap(){
    let data=resources::bundled_data();
    let baseline=recommend(data,json!({"size":10,"collab":["Draven","Katarina","Miss Fortune","Qiyana","Varus"],"mode":"high_value","min_collab":3,"limit":12}));
    let final_names=names(&baseline);
    let (level,option,replacement)=baseline["buildup"].as_array().unwrap().iter().take(9).rev().find_map(|step|
        step["temporary_options"].as_array().unwrap().iter().find_map(|option|{
            let replacement=option["options"].as_array().unwrap().iter().filter_map(Value::as_str).find(|name|
                Some(*name)!=option["selected"].as_str()&&!final_names.contains(*name))?;
            Some((step["level"].as_u64().unwrap() as usize,option,replacement))
        })).expect("The real pool must expose a genuine nonfinal manual swap");
    let slot=option["slot"].as_str().unwrap();
    assert!(!final_names.contains(option["selected"].as_str().unwrap()));
    assert_ne!(option["selected"],replacement);
    let choices=json!({(level.to_string()):{(slot):replacement}});
    let source=planner::replan_buildup(data,&baseline,Some(&choices),None,None,None).unwrap();
    assert_eq!(source["champions"],baseline["champions"]);
    assert!(names(&source["buildup"][level-1]["board"]).contains(replacement));
    let changed=alternate(data,&source,Value::Null,0,true,false).unwrap();
    assert_eq!(changed["temporary_choices"],choices);
    assert!(names(&changed["buildup"][level-1]["board"]).contains(replacement));
    assert_eq!(changed["planning_context"]["no_ad_items"],true);
    let replayed=planner::replan_buildup(data,&changed,Some(&choices),None,None,None).unwrap();
    assert_eq!(replayed["champions"],changed["champions"]);
    assert_eq!(replayed["temporary_choices"],choices);
}

#[test]
fn explicit_branch_after_local_damage_view_does_not_turn_automatic_choices_into_manual_pins(){
    let data=buildup_data();let source=buildup_source(&data);
    let phase=source["buildup"][3]["upgrades"].as_array().unwrap().len();
    let local=alternate(&data,&source,json!(4),phase,true,false).unwrap();
    assert_eq!(local["damage_alternative"]["manual_choices"],json!({}));
    assert!(!local["temporary_choices"].as_object().unwrap().is_empty());
    let phase=local["damage_alternative"]["phase"].as_u64().unwrap() as usize;
    let option=local["buildup"][3]["upgrades"][phase-1]["temporary_options"].as_array().unwrap().iter().find(|option|
        option["selected"].as_str().is_some_and(|name|name.starts_with("Magic"))
            &&option["options"].as_array().unwrap().iter().filter_map(Value::as_str).any(|name|
                name.starts_with("Magic")&&Some(name)!=option["selected"].as_str())).unwrap();
    let slot=option["slot"].as_str().unwrap();
    let champion=option["options"].as_array().unwrap().iter().filter_map(Value::as_str).find(|name|
        name.starts_with("Magic")&&Some(*name)!=option["selected"].as_str()).unwrap();
    let choices=json!({"4":{(slot):champion}});
    let selected=dispatch_with_data(&data,"choose_branch",&json!({"result":local,"level":4,"phase":phase,
        "slot":slot,"champion":champion,"temporary_choices":choices})).unwrap();
    assert_eq!(selected["temporary_choices"],choices);
    assert_eq!(selected["planning_context"]["no_ad_items"],source["planning_context"]["no_ad_items"]);
    assert!(names(phase_board(&selected,4,selected["branch_selection"]["phase"].as_u64().unwrap() as usize)).contains(champion));
    for n in 0..phase{assert_eq!(selected["buildup"][3]["upgrades"][n],local["buildup"][3]["upgrades"][n]);}
}
