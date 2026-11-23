use serde_json::{json,Value};
use std::collections::{BTreeMap,BTreeSet};
use tft_core::{dispatch_with_data,model::{GameData,OptimizeOptions},optimizer,planner,resources};

fn names(board:&Value)->BTreeSet<String>{board["champions"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect()}
fn recommend(data:&GameData,payload:Value)->Value{planner::recommend_with_buildups(data,&OptimizeOptions::from_value(&payload).unwrap()).unwrap().remove(0)}
fn phases(result:&Value)->impl Iterator<Item=&Value>{result["buildup"].as_array().unwrap().iter().flat_map(|step|std::iter::once(step).chain(step["upgrades"].as_array().unwrap()))}
fn fixture()->GameData{
    let mut champions:Vec<_>=(0..12).map(|i|json!({"name":format!("Starter{i:02}"),"cost":1,"traits":["Band"],"role":if i%2==0{"Attack Tank"}else{"Attack Caster"}})).collect();
    champions.extend([
        json!({"name":"Reward","cost":4,"traits":["Band"],"role":"Attack Caster","unlockable":true,"unlock_condition":"Have 3 Band champions.","unlock_level":"Any","unlock_source":"https://example.test/reward"}),
        json!({"name":"Variant A","cost":2,"traits":["Band"],"role":"Attack Caster","exclusive_group":"variant","unlockable":true,"unlock_condition":"Have 3 Band champions.","unlock_level":"Any","unlock_source":"https://example.test/variants"}),
        json!({"name":"Variant B","cost":2,"traits":["Band"],"role":"Attack Caster","exclusive_group":"variant","unlockable":true,"unlock_condition":"Have 3 Band champions.","unlock_level":"Any","unlock_source":"https://example.test/variants"})
    ]);
    GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[3],"unique":false},{"name":"Collab","breakpoints":[3],"unique":false}],"champions":champions})).unwrap()
}

#[test]
fn timestamps_control_actual_access_without_claiming_owned_copies(){
    let data=fixture();
    let goal=["Reward","Starter00","Starter01","Starter02","Starter03"];
    let result=recommend(&data,json!({"size":5,"locked":goal,"current_level":5,"completed_unlocks":{"Reward":5},"min_collab":0,"limit":1}));
    for step in result["buildup"].as_array().unwrap(){
        let level=step["level"].as_u64().unwrap();
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            if level<5{assert!(!names(&phase["board"]).contains("Reward"));}
            assert!(!phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Reward"&&level>=5));
        }
    }
    assert!(std::iter::once(&result["buildup"][4]).chain(result["buildup"][4]["upgrades"].as_array().unwrap()).any(|phase|names(&phase["board"]).contains("Reward")));
    let fresh=phases(&result).find(|phase|names(&phase["board"]).contains("Reward")).unwrap();
    assert_eq!(fresh["board"]["planned_stars"]["Reward"],1);
    assert!(result["buildup"][4]["preparation_boards"].as_array().unwrap().iter().all(|prep|prep["target"]!="Reward"));
    let too_early=recommend(&data,json!({"size":5,"locked":goal,"current_level":5,"completed_unlocks":{"Reward":1},"min_collab":0,"limit":1}));
    for step in too_early["buildup"].as_array().unwrap().iter().take(4){assert!(!names(&step["board"]).contains("Reward"));}
}

#[test]
fn tracked_sona_confirmation_clear_history_export_and_purity(){
    let data=resources::bundled_data();let before_data=data.raw.clone();
    let goal=["Sona","Draven","Ezreal","Garen","Katarina","Miss Fortune","Qiyana","Varus","Leona","Lillia"];
    let baseline=recommend(data,json!({"size":10,"collab":["Draven","Katarina","Miss Fortune","Qiyana","Varus"],"locked":goal,"current_level":10,"min_collab":0,"limit":1}));
    let original=baseline.clone();
    assert!(phases(&baseline).all(|phase|!names(&phase["board"]).contains("Sona")));
    let confirmed=planner::replan_buildup(data,&baseline,None,None,Some(&json!({"Sona":10})),None).unwrap();
    assert_eq!(confirmed["completed_unlocks"],json!({"Sona":10}));assert_eq!(confirmed["planning_context"]["completed_unlocks"],confirmed["completed_unlocks"]);
    assert!(std::iter::once(&confirmed["buildup"][9]).chain(confirmed["buildup"][9]["upgrades"].as_array().unwrap()).any(|phase|names(&phase["board"]).contains("Sona")));
    for step in confirmed["buildup"].as_array().unwrap().iter().take(9){for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){assert!(!names(&phase["board"]).contains("Sona"));}}
    assert!(confirmed["buildup"][9]["preparation_boards"].as_array().unwrap().iter().all(|prep|prep["target"]!="Sona"));
    assert!(phases(&confirmed).all(|phase|!phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Sona")));
    assert_eq!(planner::replan_buildup(data,&confirmed,None,None,None,None).unwrap(),confirmed);
    assert_eq!(planner::replan_buildup(data,&confirmed,Some(&Value::Null),None,Some(&Value::Null),Some(&Value::Null)).unwrap(),confirmed);
    let text=planner::format_buildup(&confirmed,data).unwrap();assert!(text.contains("User-confirmed unlock: Sona, completed at level 10"));assert!(text.contains(&data.champions["Sona"].unlock_source));
    let cleared=planner::replan_buildup(data,&confirmed,None,None,Some(&json!({})),None).unwrap();assert_eq!(cleared,baseline);
    let history=planner::replan_buildup(data,&confirmed,None,None,None,Some(&json!(8))).unwrap();assert_eq!(history["completed_unlocks"],json!({"Sona":10}));assert_eq!(history["current_level"],8);
    assert_eq!(baseline,original);assert_eq!(data.raw,before_data);
}

#[test]
fn malformed_confirmations_exclusions_and_mutual_exclusivity_are_real_constraints(){
    let data=fixture();
    for completed in [json!([]),json!({"Missing":3}),json!({"Starter00":3}),json!({"Reward":0}),json!({"Reward":8}),json!({"Reward":2.5}),json!({"Reward":"5"}),json!({"Reward":null}),json!({"Variant A":3,"Variant B":4})]{
        assert!(dispatch_with_data(&data,"recommend",&json!({"size":7,"min_collab":0,"limit":1,"completed_unlocks":completed})).is_err(),"{completed}");
    }
    for level in [9,0,11]{assert!(dispatch_with_data(resources::bundled_data(),"recommend",&json!({"size":10,"min_collab":0,"limit":1,"completed_unlocks":{"Sona":level}})).is_err());}
    assert!(dispatch_with_data(&data,"recommend",&json!({"size":7,"min_collab":0,"current_level":8})).is_err());
    assert!(dispatch_with_data(&data,"recommend",&json!({"size":7,"min_collab":0,"locked":["Variant B"],"completed_unlocks":{"Variant A":3}})).is_err());
    let excluded=recommend(&data,json!({"size":7,"excluded":["Reward"],"completed_unlocks":{"Reward":5},"current_level":7,"min_collab":0,"limit":1}));
    assert!(!names(&excluded).contains("Reward"));assert!(phases(&excluded).all(|phase|!names(&phase["board"]).contains("Reward")));
    let exclusive=recommend(&data,json!({"size":7,"completed_unlocks":{"Variant A":3},"current_level":7,"min_collab":0,"limit":1}));
    assert!(phases(&exclusive).filter(|phase|phase["board"]["level"].as_u64().unwrap_or(7)>=3).all(|phase|!names(&phase["board"]).contains("Variant B")));
}

#[test]
fn confirmed_task_does_not_require_recreating_now_excluded_helpers(){
    let mut raw=fixture().raw;raw["traits"].as_array_mut().unwrap().push(json!({"name":"Other","breakpoints":[2],"unique":false}));
    for name in ["Off A","Off B"]{raw["champions"].as_array_mut().unwrap().push(json!({"name":name,"cost":1,"traits":["Other"]}));}
    let data=GameData::from_value(raw).unwrap();let excluded:Vec<_>=(0..12).map(|i|format!("Starter{i:02}")).collect();
    let result=recommend(&data,json!({"size":3,"locked":["Variant A","Off A","Off B"],"excluded":excluded,"completed_unlocks":{"Variant A":3},"current_level":3,"min_collab":0,"limit":1}));
    assert!(names(&result["buildup"][2]["board"]).contains("Variant A"));
    assert!(result["buildup"][2]["preparation_boards"].as_array().unwrap().iter().all(|prep|prep["target"]!="Variant A"));
}

#[test]
fn role_equivalent_lucian_ezreal_share_one_slot_and_persist_until_final_pivot(){
    let data=resources::bundled_data();
    let result=recommend(data,json!({"size":10,"collab":["Draven","Katarina","Miss Fortune","Qiyana","Varus"],"locked":["Sona","Mel","Garen","Leona","Lillia","Neeko","Miss Fortune","Qiyana","Varus","Draven"],"early_availability":{"Lucian":{"level":3,"stars":1}},"min_collab":0,"limit":1}));
    let (level,slot)=result["buildup"].as_array().unwrap().iter().take(9).find_map(|step|step["temporary_options"].as_array().unwrap().iter().find(|choice|choice["selected"]=="Lucian"&&choice["options"].as_array().unwrap().iter().any(|name|name=="Ezreal")).map(|choice|(step["level"].as_u64().unwrap(),choice["slot"].as_str().unwrap().to_owned()))).expect("feasible AD-caster fallback pair");
    let choices=json!({(level.to_string()):{(slot.clone()):"Ezreal"}});
    assert!(!names(&result).contains("Lucian"));assert!(!names(&result).contains("Ezreal"));
    let pinned=planner::replan_buildup(data,&result,Some(&choices),None,None,None).unwrap();
    for step in pinned["buildup"].as_array().unwrap().iter().skip(level as usize-1){
        assert!(names(&step["board"]).contains("Ezreal"));assert!(!names(&step["board"]).contains("Lucian"));
        assert!(step["temporary_options"].as_array().unwrap().iter().any(|choice|choice["selected"]=="Ezreal"&&choice["options"].as_array().unwrap().iter().any(|name|name=="Ezreal")));
        let roster:Vec<_>=names(&step["board"]).into_iter().collect();let stars:BTreeMap<String,u8>=serde_json::from_value(step["board"]["planned_stars"].clone()).unwrap();
        let expected=optimizer::evaluate_team(data,&roster,&["Draven".into(),"Katarina".into(),"Miss Fortune".into(),"Qiyana".into(),"Varus".into()],"high_value","",&stars,Some(step["level"].as_u64().unwrap() as usize)).unwrap();
        for key in ["traits","active","tiers","planned_stars","frontline"]{assert_eq!(step["board"][key],expected[key]);}
    }
    assert_eq!(pinned["champions"],result["champions"]);assert!(!names(&pinned).contains("Ezreal"));
    assert!(pinned["buildup"][9]["upgrades"].as_array().unwrap().iter().any(|phase|phase["final_pivot"]==true&&phase["removed"].as_array().is_some_and(|names|names.iter().any(|name|name=="Ezreal"))));
    let tracked=planner::replan_buildup(data,&pinned,None,None,Some(&json!({"Sona":10})),Some(&json!(7))).unwrap();assert_eq!(tracked["temporary_choices"],choices);
    assert_eq!(planner::replan_buildup(data,&pinned,Some(&json!({})),None,None,None).unwrap(),result);
}

#[test]
fn supplied_smeech_goal_builds_joint_pivot_around_eight_without_incidental_restores(){
    let data=resources::bundled_data();
    let goal=["Alune","Evelynn","Leona","Lillia","Lucian","Miss Fortune","Neeko","Qiyana","Smeech","Sona"];
    // Varus is a representative fifth pool member; the export identified only four.
    let result=recommend(data,json!({"size":10,"collab":["Draven","Katarina","Miss Fortune","Qiyana","Varus"],"locked":goal,"stars":{"Alune":1,"Evelynn":2,"Leona":2,"Lillia":2,"Lucian":1,"Miss Fortune":1,"Neeko":2,"Qiyana":1,"Smeech":1,"Sona":1},"min_collab":0,"limit":1}));
    let eight=&result["buildup"][7];let setup=eight["preparation_boards"].as_array().unwrap().iter().find(|prep|prep["target"]=="Smeech").unwrap();
    let nine_setup=result["buildup"][8]["preparation_boards"].as_array().unwrap().iter().find(|prep|prep["target"]=="Smeech").unwrap();
    let available_setup=if setup["status"]=="conditional_setup"{setup}else{nine_setup};
    assert_eq!(available_setup["status"],"conditional_setup");assert!(available_setup["board"]["active"].as_u64().unwrap()>=8);assert!(!names(&available_setup["board"]).contains("Smeech"));
    if setup["status"]=="conditional_setup"{assert_eq!(names(&eight["board"]),names(&setup["board"]));}
    else{assert!(names(&eight["board"]).intersection(&names(&nine_setup["board"])).count()>=6,"level-eight board must already work toward the legal eight-trait check, not start an unrelated pivot at nine");}
    assert_eq!(names(&eight["board"]).iter().map(|name|data.champions[name].slots).sum::<usize>(),8);
    let stages:Vec<_>=phases(&result).collect();let acquisition=stages.iter().position(|phase|names(&phase["board"]).contains("Smeech")).unwrap();
    assert!(stages[..acquisition].iter().any(|phase|phase["board"]["active"].as_u64().unwrap_or(0)>=8&&!names(&phase["board"]).contains("Smeech")&&phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Smeech")));
    for step in result["buildup"].as_array().unwrap().iter().skip(7){
        let mut removed=BTreeSet::new();
        for phase in step["upgrades"].as_array().unwrap(){
            for name in names(&json!({"champions":phase["added"]})){
                assert!(!removed.contains(&name)||names(&result).contains(&name),"incidental restore at level {}: {name}",step["level"]);
            }
            removed.extend(names(&json!({"champions":phase["removed"]})));
        }
        if step["upgrades"].as_array().unwrap().iter().filter(|phase|phase["removed"].as_array().is_some_and(|names|!names.is_empty())).count()>2{assert_eq!(step["pivot_warning"]["fast_micro"],true);}
        let prep=step["preparation_boards"].as_array().unwrap().iter().find(|prep|prep["target"]=="Smeech").unwrap();
        if prep["scheduled"]==true{
            let phase=prep["scheduled_phase"].as_u64().unwrap() as usize;
            let board=if phase==0{&step["board"]}else{&step["upgrades"][phase-1]["board"]};
            assert_eq!(prep["board"],*board);assert!(!names(board).contains("Smeech"));
            assert!(prep["helpers"].as_array().unwrap().iter().all(|name|names(board).contains(name.as_str().unwrap())));
        }
    }
    assert_eq!(names(&result),goal.into_iter().map(str::to_owned).collect());
    assert!(planner::format_buildup(&result,data).unwrap().contains("FAST-MICRO PIVOT WARNING"));
}

#[test]
fn automatic_stars_follow_fixed_goal_context_and_ordered_thresholds(){
    let mut champions:Vec<_>=(0..12).map(|i|json!({"name":format!("Pad{i:02}"),"cost":1,"traits":["Band"],"role":"Attack Tank"})).collect();
    let mut goal=Vec::new();
    for (cost,count) in [(1,2),(2,2),(3,2),(4,2),(5,1)]{
        for i in 0..count{let name=format!("Keeper{cost}_{i}");goal.push(name.clone());champions.push(json!({"name":name,"cost":cost,"traits":["Band"],"role":if i==0{"Attack Tank"}else{"Attack Caster"}}));}
    }
    goal.push("Sona".to_owned());champions.push(json!({"name":"Sona","cost":7,"traits":["Collab"],"role":"Magic Specialist","unlockable":true,"unlock_condition":"Have Collab champions become inspired 15 times.","unlock_level":"Level 10","unlock_source":"https://example.test/sona"}));
    let data=GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2],"unique":false},{"name":"Collab","breakpoints":[3],"unique":false}],"champions":champions})).unwrap();
    let fast=recommend(&data,json!({"size":10,"locked":goal,"collab":["Keeper1_0","Keeper1_1","Keeper2_0"],"current_level":10,"min_collab":0,"limit":1}));
    for step in fast["buildup"].as_array().unwrap(){
        let level=step["level"].as_u64().unwrap() as usize;
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            for name in names(&phase["board"]){
                let rank=phase["board"]["planned_stars"][&name].as_u64().unwrap();let cost=data.champions[&name].cost;
                assert!(rank<=2,"normal Sona plan must not become reroll because the viewed board omits Sona");
                let threshold=match cost{1=>if name.starts_with("Keeper"){4}else{5},2=>6,3=>7,4=>8,5=>10,_=>usize::MAX};
                if rank>=2{assert!(level>=threshold,"{name} at level {level}");}
            }
        }
    }
    assert_eq!(fast["planned_stars"]["Sona"],1);assert_eq!(fast["planned_stars"]["Keeper4_0"],2);
    let reroll_goal:Vec<_>=goal.into_iter().filter(|name|name!="Sona").collect();
    let reroll=recommend(&data,json!({"size":9,"locked":reroll_goal,"min_collab":0,"limit":1}));
    for step in reroll["buildup"].as_array().unwrap(){
        let level=step["level"].as_u64().unwrap() as usize;
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            for name in names(&phase["board"]){
                let rank=phase["board"]["planned_stars"][&name].as_u64().unwrap();assert!(rank<4);
                if rank==3{assert!(name.starts_with("Keeper"));assert!(level>=match data.champions[&name].cost{1=>5,2=>6,3=>7,_=>panic!("non-reroll rarity received automatic three stars")});}
            }
        }
    }
    assert_eq!(reroll["planned_stars"]["Keeper1_0"],3);assert_eq!(reroll["planned_stars"]["Keeper2_0"],3);
    assert_eq!(reroll["planned_stars"]["Keeper3_0"],3);assert_eq!(reroll["planned_stars"]["Keeper4_0"],2);
    assert!(phases(&reroll).any(|phase|phase["star_upgrades"].as_array().into_iter().flatten().any(|upgrade|upgrade["to"]==3)));
}

#[test]
fn star_total_prep_waits_for_real_schedule_instead_of_inventing_four_star_helpers(){
    let mut champions:Vec<_>=(0..6).map(|i|json!({"name":format!("Kda{i}"),"cost":1,"traits":["K/DA"],"role":"Magic Tank"})).collect();
    champions.push(json!({"name":"Akali (K/DA)","cost":4,"traits":["K/DA"],"role":"Attack Fighter","unlockable":true,"unlock_condition":"Have 12 star levels of K/DA champions.","unlock_level":"Any","unlock_source":"https://example.test/kda"}));
    let data=GameData::from_value(json!({"traits":[{"name":"K/DA","breakpoints":[6,12],"unique":false,"count_mode":"stars"}],"champions":champions})).unwrap();
    let result=recommend(&data,json!({"size":7,"locked":["Akali (K/DA)","Kda0","Kda1","Kda2","Kda3","Kda4","Kda5"],"min_collab":0,"limit":1}));
    for step in result["buildup"].as_array().unwrap().iter().take(4){
        assert!(!names(&step["board"]).contains("Akali (K/DA)"));
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            for rank in phase["board"]["planned_stars"].as_object().into_iter().flatten().map(|(_,rank)|rank.as_u64().unwrap()){assert!(rank<=2);}
        }
        let prep=step["preparation_boards"].as_array().unwrap().iter().find(|prep|prep["target"]=="Akali (K/DA)").unwrap();assert!(prep["board"].is_null());
    }
    let first=phases(&result).find(|phase|phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Akali (K/DA)")).unwrap();
    assert!(!names(&first["board"]).contains("Akali (K/DA)"));assert!(names(&first["board"]).iter().map(|name|first["board"]["planned_stars"][name].as_u64().unwrap()).sum::<u64>()>=12);
    assert!(phases(&result).all(|phase|phase["board"]["planned_stars"].as_object().into_iter().flatten().all(|(_,rank)|rank.as_u64().unwrap()<4)));
}

#[test]
fn satisfied_roster_hints_reuse_actual_scheduled_board_without_confirming_task(){
    let data=fixture();
    let result=recommend(&data,json!({"size":7,"locked":["Reward"],"current_level":7,"min_collab":0,"limit":1}));
    let step=&result["buildup"][6];
    let prep=step["preparation_boards"].as_array().unwrap().iter().find(|prep|prep["target"]=="Reward").unwrap();
    assert_eq!(prep["scheduled"],true);assert_eq!(prep["scheduled_phase"],0);
    assert_eq!(prep["board"],step["board"]);
    assert!(prep["helpers"].as_array().unwrap().iter().all(|name|names(&step["board"]).contains(name.as_str().unwrap())));
    assert_eq!(prep["status"],"conditional_setup");assert_eq!(result["completed_unlocks"],json!({}));
    assert!(!names(&step["board"]).contains("Reward"));
    assert!(step["upgrades"].as_array().unwrap().iter().all(|phase|phase["unlock_checks"].as_array().into_iter().flatten().all(|check|check["name"]!="Reward")));
}

#[test]
fn temporary_one_cost_main_holders_upgrade_at_four_but_unheld_fillers_wait() {
    let mut champions:Vec<_>=(0..12).map(|i|json!({"name":format!("Temp{i:02}"),"cost":1,"traits":["Band"],
        "role":match i%3 {0=>"Magic Tank",1=>"Attack Caster",_=>"Magic Caster"}})).collect();
    let goal:Vec<String>=(0..7).map(|i|format!("Premium{i}")).collect();
    for (i,name) in goal.iter().enumerate() {
        champions.push(json!({"name":name,"cost":4,"traits":["Band"],
            "role":match i%3 {0=>"Magic Tank",1=>"Attack Caster",_=>"Magic Caster"}}));
    }
    let data=GameData::from_value(json!({"champions":champions,"traits":[
        {"name":"Band","breakpoints":[2,4],"unique":false},{"name":"Collab","breakpoints":[3],"unique":false}]})).unwrap();
    let result=recommend(&data,json!({"size":7,"locked":goal,"min_collab":0,"limit":1}));
    let step=&result["buildup"][3];
    let phase=step["upgrades"].as_array().unwrap().last().unwrap_or(step);
    let board=&phase["board"];
    let holders:BTreeSet<_>=board["item_holders"].as_object().unwrap().values().filter_map(Value::as_str).collect();
    for name in names(board) {
        assert!(name.starts_with("Temp"));
        assert_eq!(board["planned_stars"][&name],if holders.contains(name.as_str()){2}else{1},"{name}");
    }
    assert!(step["upgrades"].as_array().unwrap().iter().any(|p|p["star_upgrades"].as_array().into_iter().flatten()
        .any(|upgrade|holders.contains(upgrade["champion"].as_str().unwrap())&&upgrade["from"]==1&&upgrade["to"]==2)));
}

fn pin_preparation_fixture()->GameData{
    let mut champions:Vec<_>=(0..4).map(|i|json!({"name":format!("Band{i}"),"cost":1,"traits":["Band"],"role":"Attack Caster"})).collect();
    champions.push(json!({"name":"Starter","cost":4,"slots":2,"traits":["Other"],"role":"Magic Tank"}));
    champions.push(json!({"name":"A Early Tank","cost":1,"slots":2,"traits":["Other"],"role":"Magic Tank"}));
    for name in ["Choice A","Choice B","Choice C"]{champions.push(json!({"name":name,"cost":1,"slots":2,"traits":["Other"],"role":"Magic Tank"}));}
    for i in 0..4{champions.push(json!({"name":format!("Goal{i}"),"cost":4,"traits":["Other"],"role":"Attack Caster"}));}
    champions.push(json!({"name":"Reward","cost":3,"traits":["Other"],"role":"Attack Caster","unlockable":true,"unlock_condition":"Have 4 unique Band champions.","unlock_level":"Level 4","unlock_source":"https://example.test/band"}));
    GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2],"unique":false},{"name":"Other","breakpoints":[2],"unique":false}],"champions":champions})).unwrap()
}

fn assert_pin_until_final(result:&Value,level:usize,chosen:&str){
    let size=result["planning_context"]["size"].as_u64().unwrap() as usize;
    for step in result["buildup"].as_array().unwrap().iter().take(size-1).skip(level-1){
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            assert!(names(&phase["board"]).contains(chosen),"missing {chosen} at level {}: {}",step["level"],phase["reason"]);
        }
    }
    let final_step=&result["buildup"][size-1];
    let end=final_step["upgrades"].as_array().unwrap().last().map_or(&final_step["board"],|phase|&phase["board"]);
    assert_eq!(end,&final_step["goal_board"]);assert_eq!(names(end),names(result));assert_eq!(end["planned_stars"],result["planned_stars"]);
    if !names(result).contains(chosen){assert!(final_step["upgrades"].as_array().unwrap().iter().any(|phase|
        phase["final_pivot"]==true&&phase["pin_releases"].as_array().is_some_and(|names|names.iter().any(|name|name==chosen))));}
}

#[test]
fn first_manual_swap_defers_optional_acquisition_until_its_pinned_setup_fits(){
    let data=pin_preparation_fixture();
    let goal:Vec<_>=(0..4).map(|i|format!("Goal{i}")).chain(["Starter".to_owned(),"Reward".to_owned()]).collect();
    let baseline=recommend(&data,json!({"size":7,"locked":goal,"min_collab":0,"limit":1,
        "early_availability":{"A Early Tank":{"level":2,"stars":1}}}));
    assert_eq!(baseline["temporary_choices"],json!({}));
    assert!(names(&baseline["buildup"][2]["board"]).contains("A Early Tank"));
    assert!(!names(&baseline).contains("A Early Tank"));
    let option=baseline["buildup"][2]["temporary_options"].as_array().unwrap().iter().find(|option|
        option["selected"]!="Choice A"&&option["options"].as_array().unwrap().iter().any(|name|name=="Choice A")).unwrap();
    let slot=option["slot"].as_str().unwrap();
    let selected=planner::replan_buildup(&data,&baseline,Some(&json!({"3":{(slot):"Choice A"}})),None,None,None).unwrap();
    assert_pin_until_final(&selected,3,"Choice A");
    assert_eq!(selected["champions"],baseline["champions"]);
    assert!(selected["buildup"].as_array().unwrap().iter().take(5).all(|step|
        std::iter::once(step).chain(step["upgrades"].as_array().unwrap()).all(|phase|!names(&phase["board"]).contains("Reward"))));
    let (level,check)=selected["buildup"].as_array().unwrap().iter().find_map(|step|
        std::iter::once(step).chain(step["upgrades"].as_array().unwrap()).find(|phase|
            phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Reward")).map(|phase|(step["level"].as_u64().unwrap(),phase))).unwrap();
    assert_eq!(level,6);
    assert!(names(&check["board"]).contains("Choice A"));
    for i in 0..4{assert!(names(&check["board"]).contains(&format!("Band{i}")));}
    assert!(!names(&check["board"]).contains("Reward"));
    assert!(selected["buildup"][6]["upgrades"].as_array().unwrap().iter().any(|phase|
        phase["final_pivot"]==true&&phase["pin_releases"].as_array().is_some_and(|names|names.iter().any(|name|name=="Choice A"))));
    assert!(!option["options"].as_array().unwrap().iter().any(|name|name=="Goal0"));
    assert!(planner::replan_buildup(&data,&baseline,Some(&json!({"3":{(slot):"Goal0"}})),None,None,None).is_err());
}

#[test]
fn changing_an_existing_pin_preserves_earlier_choice_and_future_setup_legality(){
    let data=pin_preparation_fixture();
    let goal:Vec<_>=(0..4).map(|i|format!("Goal{i}")).chain(["Starter".to_owned(),"Reward".to_owned()]).collect();
    let baseline=recommend(&data,json!({"size":7,"locked":goal,"min_collab":0,"limit":1,
        "early_availability":{"A Early Tank":{"level":2,"stars":1}}}));
    assert_eq!(baseline["temporary_choices"],json!({}));
    assert!(names(&baseline["buildup"][2]["board"]).contains("A Early Tank"));
    assert!(!names(&baseline).contains("A Early Tank"));
    let slot=baseline["buildup"][2]["temporary_options"].as_array().unwrap().iter().find(|option|
        option["selected"]!="Choice A"&&option["options"].as_array().unwrap().iter().any(|name|name=="Choice A")).unwrap()["slot"].as_str().unwrap();
    let first=planner::replan_buildup(&data,&baseline,Some(&json!({"3":{(slot):"Choice A"}})),None,None,None).unwrap();
    let changed=planner::replan_buildup(&data,&first,Some(&json!({"3":{(slot):"Choice A"},"4":{(slot):"Choice B"},"6":{(slot):"Choice C"}})),None,None,None).unwrap();
    assert_eq!(changed["buildup"][2],first["buildup"][2]);
    assert_eq!(changed["temporary_choices"],json!({"3":{(slot):"Choice A"},"4":{(slot):"Choice B"},"6":{(slot):"Choice C"}}));
    assert_eq!(changed["temporary_slots"]["3"],first["temporary_slots"]["3"]);
    for level in [4,5]{for phase in std::iter::once(&changed["buildup"][level-1]).chain(changed["buildup"][level-1]["upgrades"].as_array().unwrap()){assert!(names(&phase["board"]).contains("Choice B"));}}
    assert_pin_until_final(&changed,6,"Choice C");
    assert_eq!(changed["champions"],baseline["champions"]);
    let check=phases(&changed).find(|phase|phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Reward")).unwrap();
    assert!(names(&check["board"]).contains("Choice C"));
    for i in 0..4{assert!(names(&check["board"]).contains(&format!("Band{i}")));}
}

#[test]
fn reported_fresh_pool_current_and_offered_nonfinal_units_are_valid_first_pins(){
    let data=resources::bundled_data();
    let baseline=recommend(data,json!({"size":10,"collab":["Draven","Katarina","Miss Fortune","Qiyana","Varus"],"mode":"high_value","min_collab":3,"limit":12}));
    let final_names=names(&baseline);
    for current in [true,false]{
        let (level,option,name)=baseline["buildup"].as_array().unwrap().iter().take(7).skip(2).rev().find_map(|step|
            step["temporary_options"].as_array().unwrap().iter().find_map(|option|{
                let selected=option["selected"].as_str().unwrap();
                let name=if current{selected}else{option["options"].as_array().unwrap().iter().filter_map(Value::as_str)
                    .find(|name|*name!=selected&&!final_names.contains(*name))?};
                Some((step["level"].as_u64().unwrap() as usize,option,name))
            })).expect("The real pool must offer a genuine nonfinal manual slot");
        let slot=option["slot"].as_str().unwrap();
        assert!(!final_names.contains(option["selected"].as_str().unwrap()));assert!(!final_names.contains(name));
        if !current{assert_ne!(option["selected"],name);}
        let selected=planner::replan_buildup(data,&baseline,Some(&json!({(level.to_string()):{(slot):name}})),None,None,None).unwrap();
        assert_pin_until_final(&selected,level,name);
        assert_eq!(selected["champions"],baseline["champions"]);
        assert_eq!(selected["temporary_choices"],json!({(level.to_string()):{(slot):name}}));
    }
}

#[test]
fn adaptive_branch_routes_keep_real_unlock_preparation_and_fixed_goal_tracking(){
    let mut raw=fixture().raw;
    raw["champions"].as_array_mut().unwrap().extend((0..6).map(|i|
        json!({"name":format!("Premium{i}"),"cost":4,"traits":["Band"],"role":"Attack Tank"})));
    raw["champions"].as_array_mut().unwrap().extend((0..8).map(|i|
        json!({"name":format!("Branch{i}"),"cost":3,"traits":["Band"],"role":"Attack Caster"})));
    let data=GameData::from_value(raw).unwrap();
    let goal:Vec<_>=std::iter::once("Reward".to_owned()).chain((0..6).map(|i|format!("Premium{i}"))).collect();
    let mut source=recommend(&data,json!({"size":7,"locked":goal,"min_collab":0,"limit":1}));
    source["planning_context"]["locked"]=json!(["Reward"]);
    let (slot,champion)=source["buildup"][3]["temporary_options"].as_array().unwrap().iter().find_map(|option|
        option["options"].as_array().unwrap().iter().filter_map(Value::as_str)
            .find(|name|name.starts_with("Branch")&&Some(*name)!=option["selected"].as_str())
            .map(|name|(option["slot"].as_str().unwrap().to_owned(),name.to_owned()))).unwrap();
    let offered=source["buildup"][3]["temporary_options"].as_array().unwrap().iter().find(|option|option["slot"]==slot).unwrap();
    assert!(offered["routes"][&champion]["unlock_targets"].as_array().unwrap().iter().any(|name|name=="Reward"));
    let selected=dispatch_with_data(&data,"choose_branch",&json!({"result":source,"level":4,"phase":0,
        "slot":slot,"champion":champion,"temporary_choices":{"4":{(slot.clone()):champion}}})).unwrap();
    assert!(names(&selected).contains(&champion));assert!(names(&selected).contains("Reward"));
    assert_eq!(selected["completed_unlocks"],json!({}));
    let history:Vec<_>=phases(&selected).collect();
    let first=history.iter().position(|phase|names(&phase["board"]).contains("Reward")).unwrap();
    assert!(history[..first].iter().any(|phase|phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Reward")));
    assert!(history[..first].iter().any(|phase|!names(&phase["board"]).contains("Reward")&&names(&phase["board"]).len()>=3));
    let replay=planner::replan_buildup(&data,&selected,None,None,Some(&json!({"Reward":7})),Some(&json!(6))).unwrap();
    assert_eq!(replay["champions"],selected["champions"]);
    assert_eq!(replay["planned_stars"],selected["planned_stars"]);
    assert_eq!(replay["temporary_choices"],selected["temporary_choices"]);
    let end=&replay["buildup"][6];let end=end["upgrades"].as_array().unwrap().last().map_or(&end["board"],|phase|&phase["board"]);
    assert_eq!(end["champions"],replay["champions"]);assert_eq!(end["planned_stars"],replay["planned_stars"]);
}
