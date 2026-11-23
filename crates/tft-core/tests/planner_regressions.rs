use serde_json::{json,Value};
use std::collections::{BTreeMap,BTreeSet};
use std::sync::LazyLock;
use tft_core::{model::{GameData,OptimizeOptions},optimizer,planner,resources};

fn options(value:Value)->OptimizeOptions {OptimizeOptions::from_value(&value).unwrap()}
fn names(board:&Value)->BTreeSet<String>{board["champions"].as_array().into_iter().flatten().map(|n|n.as_str().unwrap().to_owned()).collect()}
fn board_slots(data:&GameData,board:&Value)->usize{names(board).iter().map(|n|data.champions[n].slots).sum()}
fn choice_data()->GameData{
    let mut champions=Vec::new();
    for i in 0..12{champions.push(json!({"name":format!("Temp{i:02}"),"cost":1,"traits":["Band"],"role":if i%2==0{"Attack Tank"}else{"Attack Marksman"}}));}
    for i in 0..7{champions.push(json!({"name":format!("Goal{i}"),"cost":4,"traits":["Band"],"role":if i%2==0{"Attack Tank"}else{"Attack Marksman"}}));}
    GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2,4],"unique":false},{"name":"Collab","breakpoints":[3],"unique":false}],"champions":champions})).unwrap()
}
fn choice_result(data:&GameData)->Value{
    planner::recommend_with_buildups(data,&options(json!({"collab":[],"size":7,"locked":(0..7).map(|i|format!("Goal{i}")).collect::<Vec<_>>(),"excluded":["Temp11"],"min_collab":0,"mode":"balanced","limit":1,"stars":{"Temp08":3}}))).unwrap().remove(0)
}
static SCREENSHOT:LazyLock<Value>=LazyLock::new(||{
    planner::recommend_with_buildups(resources::bundled_data(),&options(json!({"collab":["Garen","Ezreal","Lucian"],"size":10,"locked":["Garen","Rell","Thresh","Galio","Viego","Smeech","Vladimir","Ezreal","Lucian","Mel"],"min_collab":0,"mode":"balanced","limit":1,"stars":{"Garen":2,"Thresh":2}}))).unwrap().remove(0)
});
fn prep<'a>(step:&'a Value,target:&str)->&'a Value{step["preparation_boards"].as_array().unwrap().iter().find(|p|p["target"]==target).unwrap()}
fn actual_phases(result:&Value)->Vec<&Value>{result["buildup"].as_array().unwrap().iter().flat_map(|s|std::iter::once(s).chain(s["upgrades"].as_array().unwrap().iter())).collect()}
fn assert_explicit_stars(result:&Value){
    let mut previous=BTreeMap::new();
    for phase in actual_phases(result){if phase["board"].is_null(){continue}let ranks=phase["board"]["planned_stars"].as_object().unwrap();
        for (name,rank) in ranks{let from=previous.get(name).copied().unwrap_or(1);let to=rank.as_u64().unwrap();
            if to>from{assert!(phase["star_upgrades"].as_array().into_iter().flatten().any(|u|u["champion"]==*name&&u["from"]==from&&u["to"]==to),"{name}: {from} → {to}");}
            if to<from{assert!(phase["copy_replacements"].as_array().into_iter().flatten().any(|u|u["champion"]==*name&&u["from"]==from&&u["to"]==to));}
        }
        previous=ranks.iter().map(|(n,r)|(n.clone(),r.as_u64().unwrap())).collect();
    }
}

#[test]
fn late_carry_preference_reaches_level_ten_buildups() {
    let mut champions:Vec<_>=(0..10).map(|i|json!({"name":format!("Cheap{i}"),"cost":1,"traits":[],"role":"Attack Marksman"})).collect();
    champions.push(json!({"name":"Carry","cost":3,"traits":[],"role":"Magic Caster"}));
    let data=GameData::from_value(json!({"traits":[],"champions":champions})).unwrap();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":10,"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    assert!(names(&result).contains("Carry"));
    let final_stars:BTreeMap<String,u8>=serde_json::from_value(result["planned_stars"].clone()).unwrap();
    assert_eq!(result["score"],optimizer::evaluate_team(&data,&names(&result).into_iter().collect::<Vec<_>>(),&[],"balanced","",&final_stars,Some(10)).unwrap()["score"]);
    for step in result["buildup"].as_array().unwrap() {
        let level=step["level"].as_u64().unwrap() as usize;
        let board=&step["board"];
        if !board.is_null() {
            let stars:BTreeMap<String,u8>=serde_json::from_value(board["planned_stars"].clone()).unwrap();
            let expected=optimizer::evaluate_team(&data,&names(board).into_iter().collect::<Vec<_>>(),&[],"balanced","",&stars,Some(level)).unwrap();
            assert_eq!(board["score"],expected["score"]);
        }
    }
}

#[test]
fn actual_classical_preparation_repeats_before_thresh_arrives(){
    let result=&*SCREENSHOT;
    let first=result["buildup"].as_array().unwrap().iter().find(|s|std::iter::once(*s).chain(s["upgrades"].as_array().unwrap()).any(|p|names(&p["board"]).contains("Thresh"))).unwrap()["level"].as_u64().unwrap();
    let setups:Vec<_>=result["buildup"].as_array().unwrap().iter().filter(|s|!prep(s,"Thresh")["board"].is_null()).collect();
    assert_eq!(setups[0]["level"],3);
    assert!(setups.iter().filter(|s|s["level"].as_u64().unwrap()<first).count()>1);
    for step in setups{let setup=prep(step,"Thresh");assert!(!names(&setup["board"]).contains("Thresh"));assert_eq!(setup["accumulating"],true);assert!(setup["condition"].as_str().unwrap().contains("300"));assert!(setup["board"]["traits"].as_array().unwrap().iter().any(|t|t["name"]=="Classical"&&t["tier"].as_u64().unwrap()>0));}
}
#[test]
fn smeech_and_published_level_eight_vladimir_have_real_prior_boards(){
    let result=&*SCREENSHOT;
    for target in ["Smeech","Vladimir"]{
        let phases=actual_phases(result);let first=phases.iter().position(|p|names(&p["board"]).contains(target)).unwrap();
        let setups:Vec<_>=phases[..first].iter().filter(|p|!names(&p["board"]).contains(target)&&if target=="Smeech"{p["board"]["active"].as_u64().unwrap_or(0)>=8}else{p["board"]["positioning_constraint"]=="one_front_two_rows"}).collect();
        assert!(!setups.is_empty(),"{target}");
    }
    for step in result["buildup"].as_array().unwrap(){let setup=prep(step,"Vladimir");assert_eq!(setup["board"].is_null(),step["level"].as_u64().unwrap()<8);}
}
#[test]
fn chosen_classical_helper_influences_actual_preparation(){
    let mut champions:Vec<_>=(0..4).map(|i|json!({"name":format!("Classical{i}"),"cost":1,"traits":["Classical"],"role":"Magic Tank"})).collect();
    for i in 0..8 { champions.push(json!({"name":format!("Temp{i}"),"cost":1,"traits":["Other"],"role":"Attack Caster"})); }
    for i in 0..7 { champions.push(json!({"name":format!("Goal{i}"),"cost":4,"traits":["Other"],"role":"Attack Caster"})); }
    champions.push(json!({"name":"Thresh","cost":4,"traits":["Classical"],"role":"Magic Tank","unlockable":true,
        "unlock_level":"Any","unlock_condition":"Have enemies spend 300 seconds Captivated by Classical.","unlock_source":"https://example.test/thresh"}));
    let data=GameData::from_value(json!({"champions":champions,"traits":[{"name":"Classical","breakpoints":[2],"unique":false},
        {"name":"Other","breakpoints":[2],"unique":false},{"name":"Collab","breakpoints":[3],"unique":false}]})).unwrap();
    let goal:Vec<_>=std::iter::once("Thresh".to_owned()).chain((0..7).map(|i|format!("Goal{i}"))).collect();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":8,"locked":goal,"min_collab":0,"limit":1}))).unwrap().remove(0);
    let (level,slot,replacement)=result["buildup"].as_array().unwrap().iter().find_map(|step| {
        if prep(step,"Thresh")["board"].is_null(){return None;}
        step["temporary_options"].as_array().unwrap().iter().find_map(|option| {
            option["options"].as_array().unwrap().iter().filter_map(Value::as_str)
                .find(|name|*name!=option["selected"].as_str().unwrap()&&data.champions[*name].traits.iter().any(|t|t=="Classical"))
                .map(|name|(step["level"].as_u64().unwrap() as usize,option["slot"].as_str().unwrap().to_owned(),name.to_owned()))
        })
    }).expect("The controlled fixture must offer an interchangeable Classical helper.");
    let fresh=planner::replan_buildup(&data,&result,Some(&json!({(level.to_string()):{(slot):replacement}})),None,None,None).unwrap();
    assert!(names(&prep(&fresh["buildup"][level-1],"Thresh")["board"]).contains(&replacement));
    assert_eq!(fresh["champions"],result["champions"]);
}
#[test]
fn full_path_retains_units_and_reaches_exact_goal_with_shared_stats(){
    let data=resources::bundled_data();let result=&*SCREENSHOT;let mut previous=BTreeSet::new();let mut fallback_previous=BTreeSet::new();
    for step in result["buildup"].as_array().unwrap(){let level=step["level"].as_u64().unwrap() as usize;let current=names(&step["board"]);let fallback=names(&step["fallback"]);assert_eq!(step["removed"],json!(previous.difference(&current).collect::<Vec<_>>()));assert!(fallback_previous.difference(&fallback).count()<=if level>=8{2}else{1});
        let mut boards=vec![&step["board"],&step["fallback"]];if !step["goal_board"].is_null(){boards.push(&step["goal_board"]);}for phase in step["upgrades"].as_array().unwrap(){boards.push(&phase["board"]);assert_eq!(phase["stars"],phase["board"]["planned_stars"]);}
        for board in boards{if board.is_null(){continue}assert_eq!(board_slots(data,board),level);let board_names:Vec<_>=names(board).into_iter().collect();let stars:BTreeMap<String,u8>=serde_json::from_value(board["planned_stars"].clone()).unwrap();let expected=optimizer::evaluate_team(data,&board_names,&["Garen".into(),"Ezreal".into(),"Lucian".into()],"balanced","",&stars,Some(level)).unwrap();for (key,value) in expected.as_object().unwrap(){assert_eq!(&board[key],value,"level {level}, {key}");}}
        for name in &fallback{assert!(!data.champions[name].unlockable);}previous=step["upgrades"].as_array().unwrap().last().map_or(current,|p|names(&p["board"]));fallback_previous=fallback;
    }
    let final_step=&result["buildup"][9];let end=final_step["upgrades"].as_array().unwrap().last().map_or(&final_step["board"],|p|&p["board"]);assert_eq!(end,&final_step["goal_board"]);assert_eq!(names(end),names(result));assert_eq!(result["planned_stars"]["Thresh"],2);
}
#[test]
fn choices_are_pure_stable_and_propagate_without_changing_goal(){
    let data=choice_data();let result=choice_result(&data);let before=result.clone();let first=&result["buildup"][0];let option=first["temporary_options"].as_array().unwrap().iter().find(|o|o["options"].as_array().unwrap().len()>1).unwrap();let slot=option["slot"].as_str().unwrap();let selected=option["options"].as_array().unwrap().iter().filter_map(Value::as_str).find(|n|*n!=option["selected"].as_str().unwrap()&&!names(&result["buildup"][1]["board"]).contains(*n)).unwrap();
    assert_eq!(option["planned_stars"][selected],1);
    let choices=json!({"1":{(slot):selected}});let fresh=planner::replan_buildup(&data,&result,Some(&choices),None,None,None).unwrap();assert!(names(&fresh["buildup"][0]["board"]).contains(selected));assert!(fresh["buildup"][1]["retained"].as_array().unwrap().iter().any(|n|n==selected));assert_eq!(fresh["champions"],result["champions"]);assert_eq!(fresh["buildup"][6]["goal_board"],result["buildup"][6]["goal_board"]);assert_eq!(fresh["temporary_choices"],choices);assert_eq!(result,before);assert_eq!(planner::replan_buildup(&data,&result,None,None,None,None).unwrap(),result);
    assert!(planner::format_buildup(&fresh,&data).unwrap().contains("Temporary choice"));
}
#[test]
fn invalid_temporary_choices_are_errors_not_ignored(){
    let data=choice_data();let result=choice_result(&data);let slot=result["buildup"][0]["temporary_options"][0]["slot"].as_str().unwrap();
    for choices in [json!([]),json!({"0":{}}),json!({"01":{}}),json!({"1":[]}),json!({"1":{"missing":"Temp02"}}),json!({"1":{(slot):"Unknown"}}),json!({"1":{(slot):"Goal0"}}),json!({"1":{(slot):"Temp11"}}),json!({"1":{(slot):null}})]{assert!(planner::replan_buildup(&data,&result,Some(&choices),None,None,None).is_err(),"{choices}");}
}
#[test]
fn fielded_final_members_are_hidden_protected_and_still_accept_explicit_identity_choices(){
    let data=choice_data();let result=choice_result(&data);let target=names(&result);
    for step in result["buildup"].as_array().unwrap(){
        for option in step["temporary_options"].as_array().unwrap(){
            assert!(!target.contains(option["selected"].as_str().unwrap()));
        }
    }
    let step=&result["buildup"][6];let current=names(&step["board"]);
    let final_name=current.intersection(&target).next().expect("An eligible Final member must arrive before the fixed final pivot.");
    assert!(result["temporary_slots"]["7"].as_array().unwrap().iter().any(|slot|slot==final_name));
    let replacement=data.champions.keys().find(|name|!target.contains(*name)&&!current.contains(*name)&&name.as_str()!="Temp11").unwrap();
    let forged=json!({"7":{(final_name):replacement}});
    let error=planner::replan_buildup(&data,&result,Some(&forged),None,None,None).unwrap_err();
    assert!(error.contains("selected Final roster are protected"),"{error}");
    let identity=json!({"7":{(final_name):final_name}});
    let accepted=planner::replan_buildup(&data,&result,Some(&identity),None,None,None).unwrap();
    assert_eq!(accepted["temporary_choices"],identity);assert_eq!(accepted["champions"],result["champions"]);
    assert!(!accepted["buildup"][6]["temporary_options"].as_array().unwrap().iter().any(|option|option["selected"]==final_name.as_str()));
    assert_eq!(planner::replan_buildup(&data,&accepted,None,None,None,None).unwrap(),accepted);
}
#[test]
fn conservative_gates_and_explicit_unlock_data_are_enforced(){
    let data=resources::bundled_data();
    for (level,name,minimum) in [(6,"Garen",7),(8,"Rell",9),(9,"Sona",10),(8,"Baron Nashor",9)]{let error=planner::recommend_with_buildups(data,&options(json!({"size":level,"locked":[name],"min_collab":0,"limit":1}))).unwrap_err();assert!(error.contains(&format!("planning level {minimum}")),"{error}");}
    let mut raw=data.raw.clone();raw["champions"].as_array_mut().unwrap().iter_mut().find(|c|c["name"]=="Sona").unwrap().as_object_mut().unwrap().remove("unlock_source");let bad=GameData::from_value(raw).unwrap();assert!(planner::recommend_with_buildups(&bad,&options(json!({"min_collab":0,"limit":1}))).unwrap_err().contains("Sona is missing unlock_source"));
}
#[test]
fn excluded_classical_setup_is_unavailable_without_using_thresh_itself(){
    let data=resources::bundled_data();let excluded:Vec<_>=data.champions.values().filter(|c|c.name!="Thresh"&&c.traits.iter().any(|t|t=="Classical")).map(|c|c.name.clone()).collect();
    let error=planner::recommend_with_buildups(data,&options(json!({"size":7,"locked":["Thresh"],"excluded":excluded,"min_collab":0,"mode":"balanced","limit":1}))).unwrap_err();
    assert!(error.contains("Unreachable unlock prerequisite for Thresh"),"{error}");
}
#[test]
fn star_total_recipe_uses_real_trait_members_and_export_preserves_legal_sources(){
    let data=resources::bundled_data();let result=planner::recommend_with_buildups(data,&options(json!({"size":7,"locked":["Yasuo"],"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);let setup=prep(&result["buildup"][6],"Yasuo");assert!(!setup["board"].is_null());assert!(!names(&setup["board"]).contains("Yasuo"));assert!(setup["board"]["traits"].as_array().unwrap().iter().any(|t|["True Damage","Slayer"].contains(&t["name"].as_str().unwrap())));
    let text=planner::format_buildup(&SCREENSHOT,data).unwrap();for expected in ["PREPARE BEFORE ARRIVAL","Actual unlock setup for Thresh","300 seconds","one","Planned ","Item plan source:","Exact final-level goal","Thresh (★★, conditional unlock)",planner::SHOP_SOURCE,resources::metadata()["legal"]["purpose"].as_str().unwrap(),resources::metadata()["legal"]["liability"].as_str().unwrap()]{assert!(text.contains(expected),"missing {expected}");}
}
#[test]
fn prospective_final_level_temporary_options_use_requested_stars(){
    let data=choice_data();let result=choice_result(&data);
    for option in result["buildup"][6]["temporary_options"].as_array().unwrap(){for name in option["options"].as_array().unwrap().iter().filter_map(Value::as_str){assert_eq!(option["planned_stars"][name],if name=="Temp08"{3}else if data.champions[name].cost==1{2}else{1});}}
}

#[test]
fn distinct_source_wording_prepares_real_units_not_star_substitutes(){
    for (goal,count,band,secondary,cost,level) in [("Evelynn",3,"K/DA","Renegade",3,6),("Alune",4,"Heartsteel","Prodigy",4,8),("Kayle",4,"Pentakill","Swiftstriker",4,8),("Zac",5,"Beach Pop","Juggernaut",4,8)] {
        let mut champions=Vec::new();
        for i in 0..count{champions.push(json!({"name":format!("Helper {i}"),"cost":1,"traits":[band]}));}
        for i in 0..12{champions.push(json!({"name":format!("Filler {i}"),"cost":1,"traits":["Padding"]}));}
        champions.push(json!({"name":goal,"cost":cost,"traits":[band],"unlockable":true,"unlock_condition":format!("Have {count} unique {band} or {secondary} champions."),"unlock_level":if cost==4{"Level 7"}else{"Any"},"unlock_source":"https://example.test/distinct"}));
        let data=GameData::from_value(json!({"traits":[{"name":band,"breakpoints":[count],"unique":false,"count_mode":if band=="K/DA"{"stars"}else{"units"}},{"name":secondary,"breakpoints":[2],"unique":false},{"name":"Padding","breakpoints":[99],"unique":false}],"champions":champions})).unwrap();
        let result=planner::recommend_with_buildups(&data,&options(json!({"size":level,"locked":[goal],"min_collab":0,"mode":"balanced","limit":1,"stars":{"Helper 0":4}}))).unwrap().remove(0);
        let helpers:BTreeSet<_>=(0..count).map(|i|format!("Helper {i}")).collect();
        let phases=actual_phases(&result);let first=phases.iter().position(|s|names(&s["board"]).contains(goal)).unwrap();
        assert!(phases[..first].iter().any(|s|helpers.is_subset(&names(&s["board"]))));
        assert!(phases[..first].iter().any(|s|s["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]==goal)));
        let preparation=result["buildup"].as_array().unwrap().iter().flat_map(|s|s["unlock_prep"].as_array().unwrap()).find(|p|p["name"]==goal).unwrap();
        assert_eq!(preparation["count"],count);assert_eq!(preparation["count_mode"],"distinct_champions");
    }
}
#[test]
fn named_off_trait_helper_is_fielded_before_conditional_arrival(){
    let data=resources::bundled_data();let result=planner::recommend_with_buildups(data,&options(json!({"size":7,"locked":["Tibbers"],"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    let phases=actual_phases(&result);let first=phases.iter().position(|s|names(&s["board"]).contains("Tibbers")).unwrap();
    assert!(phases[..first].iter().any(|s|s["board"]["planned_stars"]["Annie"]==2));
    assert!(phases[..first].iter().any(|s|s["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Tibbers")));
    assert!(planner::format_buildup(&result,data).unwrap().contains("replace after unlock is complete"));
}
#[test]
fn manual_hyperpop_chain_allows_guide_dependencies_not_unrelated_final_chases(){
    let data=resources::bundled_data();let result=planner::recommend_with_buildups(data,&options(json!({"size":10,"locked":["Teemo"],"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    let goal=names(&result);assert!(goal.contains("Teemo"));for name in ["Kobuko","Lulu","Tristana"]{assert!(!goal.contains(name));}
    assert!(actual_phases(&result).iter().any(|s|names(&s["board"]).contains("Kobuko")));
    assert!(planner::format_buildup(&result,data).unwrap().contains("Manual chain lock: Teemo"));
}
#[test]
fn unavailable_exact_slot_primary_and_fallback_are_explicit(){
    let data=GameData::from_value(json!({"traits":[{"name":"Arcane","breakpoints":[2],"unique":false}],"champions":[{"name":"Only","cost":3,"slots":4,"traits":["Arcane"],"unlockable":true,"unlock_condition":"Perform a task.","unlock_level":"Any","unlock_source":"https://example.test"}]})).unwrap();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":4,"locked":["Only"],"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    for (i,step) in result["buildup"].as_array().unwrap().iter().enumerate(){assert!(step["fallback"].is_null());if i<3{assert!(step["board"].is_null());assert!(step["reason"].as_str().unwrap().contains("No feasible exact-slot board"));}}
}
#[test]
fn final_and_level_ten_stars_and_level_eleven_cap_are_explicit(){
    let mut data=choice_data();let mut raw=data.raw.clone();raw["champions"].as_array_mut().unwrap().push(json!({"name":"KDA Tank","cost":4,"traits":["Band"],"role":"Magic Tank"}));data=GameData::from_value(raw).unwrap();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":11,"locked":["KDA Tank"],"min_collab":0,"mode":"balanced","limit":1,"stars":{"Temp08":4}}))).unwrap().remove(0);
    assert_explicit_stars(&result);
    assert_eq!(result["planned_stars"]["KDA Tank"],2);assert_eq!(result["frontline_anchor"],"Temp08");assert!(planner::format_buildup(&result,&data).unwrap().contains("special level-cap extension"));
}

#[test]
fn actual_akali_path_fields_twelve_stars_before_conditional_handoff(){
    let data=resources::bundled_data();
    let goal=["Leona","Garen","Ahri","Akali (K/DA)","Nidalee","Lillia","Sona","Syndra","Neeko","Varus"];
    let result=planner::recommend_with_buildups(data,&options(json!({"size":10,"collab":["Varus","Ahri","Kennen","Graves","Gwen"],"locked":goal,"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    let phases=actual_phases(&result);let first=phases.iter().position(|p|names(&p["board"]).contains("Akali (K/DA)")).unwrap();
    let stars=|board:&Value|names(board).iter().filter(|n|n.as_str()!="Akali (K/DA)"&&data.champions[*n].traits.iter().any(|t|t=="K/DA")).map(|n|board["planned_stars"][n].as_u64().unwrap()).sum::<u64>();
    assert!(phases[..first].iter().any(|p|stars(&p["board"])>=12));
    assert!(phases[..first].iter().any(|p|p["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Akali (K/DA)")&&stars(&p["board"])>=12));
    assert_explicit_stars(&result);assert_eq!(names(&result),goal.iter().map(|n|(*n).to_owned()).collect());
    assert_eq!(planner::replan_buildup(data,&result,None,None,None,None).unwrap(),result);
    let text=planner::format_buildup(&result,data).unwrap();assert!(text.contains("Plan star upgrade"));assert!(text.contains("CHECK BEFORE BUYING"));
}

#[test]
fn another_star_total_and_impossible_known_setup_are_enforced(){
    let data=resources::bundled_data();
    let result=planner::recommend_with_buildups(data,&options(json!({"size":10,"locked":["Mordekaiser"],"min_collab":0,"mode":"balanced","limit":1}))).unwrap().remove(0);
    let phases=actual_phases(&result);let first=phases.iter().position(|p|names(&p["board"]).contains("Mordekaiser")).unwrap();
    assert!(phases[..first].iter().any(|p|names(&p["board"]).iter().filter(|n|data.champions[*n].traits.iter().any(|t|t=="Pentakill")).map(|n|p["board"]["planned_stars"][n].as_u64().unwrap()).sum::<u64>()>=10));
    assert_explicit_stars(&result);
    let excluded:Vec<_>=data.champions.values().filter(|c|c.name!="Akali (K/DA)"&&c.traits.iter().any(|t|t=="K/DA")).map(|c|c.name.clone()).collect();
    assert!(planner::recommend_with_buildups(data,&options(json!({"size":10,"locked":["Akali (K/DA)"],"excluded":excluded,"min_collab":0,"limit":1}))).unwrap_err().contains("Unreachable unlock prerequisite"));
}

#[test]
fn preparation_uses_scheduled_ranks_and_respects_explicit_final_ranks(){
    let mut champions:Vec<_>=["A","B","C"].iter().map(|n|json!({"name":n,"cost":1,"traits":["Band"]})).collect();
    champions.push(json!({"name":"Filler","cost":1,"traits":[]}));
    champions.push(json!({"name":"Reward","cost":2,"traits":["Band"],"unlockable":true,"unlock_condition":"Have 5 star levels of Band champions.","unlock_level":"Any","unlock_source":"https://example.test/stars"}));
    let data=GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[3],"unique":false,"count_mode":"stars"}],"champions":champions})).unwrap();
    for explicit in [false,true]{
        let result=planner::recommend_with_buildups(&data,&options(json!({"size":4,"locked":["A","B","C","Reward"],"min_collab":0,"limit":1,"stars":if explicit{json!({"A":1})}else{json!({})}}))).unwrap().remove(0);
        assert_explicit_stars(&result);assert_eq!(result["planned_stars"]["A"],if explicit{1}else{2});
        assert_eq!(planner::replan_buildup(&data,&result,None,None,None,None).unwrap(),result);
        assert!(!actual_phases(&result).iter().any(|p|p["copy_replacements"].as_array().is_some_and(|a|!a.is_empty())));
    }
}

#[test]
fn globally_impossible_incidental_unlock_is_filtered_before_selecting_locked_sona(){
    let mut champions:Vec<_>=(0..12).map(|i|json!({"name":format!("Ordinary{i}"),"cost":1,"traits":if i<3{vec!["K/DA"]}else{vec![]}})).collect();
    champions.push(json!({"name":"Akali (K/DA)","cost":5,"traits":["K/DA"],"exclusive_group":"akali","unlockable":true,"unlock_condition":"Have 12 unique star levels of K/DA without True Damage Akali unlocked.","unlock_level":"Any","unlock_source":"https://example.test/stars"}));
    champions.push(json!({"name":"Sona","cost":7,"traits":[],"unlockable":true,"unlock_condition":"Have Collab champions become inspired 15 times.","unlock_level":"Level 10","unlock_source":"https://example.test/sona"}));
    let data=GameData::from_value(json!({"traits":[{"name":"K/DA","breakpoints":[3],"count_mode":"stars","unique":false},{"name":"Collab","breakpoints":[3],"unique":false}],"champions":champions})).unwrap();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":10,"collab":["Ordinary0","Ordinary3","Ordinary4"],"locked":["Sona"],"min_collab":0,"mode":"high_value","limit":1}))).unwrap().remove(0);
    assert!(names(&result).contains("Sona"));assert!(!names(&result).contains("Akali (K/DA)"));
    assert!(actual_phases(&result).iter().any(|phase|phase["unlock_checks"].as_array().into_iter().flatten().any(|check|check["name"]=="Sona")));
    let error=planner::recommend_with_buildups(&data,&options(json!({"size":10,"locked":["Akali (K/DA)"],"min_collab":0,"limit":1}))).unwrap_err();
    assert!(error.contains("Unreachable unlock prerequisite for Akali (K/DA)"),"{error}");
}

#[test]
fn early_final_carry_changes_primary_with_explicit_ranks_and_clear_is_pure(){
    let data=choice_data();let original_data=data.raw.clone();let baseline=choice_result(&data);let original=baseline.clone();
    let assumptions=json!({"Goal0":{"level":3,"stars":3}});
    let scenario=planner::replan_buildup(&data,&baseline,Some(&json!({})),Some(&assumptions),None,None).unwrap();
    assert_eq!(scenario["champions"],baseline["champions"]);
    assert_eq!(scenario["early_availability"],assumptions);assert_eq!(scenario["planning_context"]["early_availability"],assumptions);
    assert!(!names(&scenario["buildup"][1]["board"]).contains("Goal0"));
    assert!(names(&scenario["buildup"][2]["board"]).contains("Goal0"));
    assert!(actual_phases(&scenario).iter().any(|phase|phase["star_upgrades"].as_array().into_iter().flatten().any(|u|u["champion"]=="Goal0"&&u["from"]==1&&u["to"]==3)));
    assert_eq!(scenario["planned_stars"]["Goal0"],3);assert_explicit_stars(&scenario);
    let end=scenario["buildup"][6]["upgrades"].as_array().unwrap().last().map_or(&scenario["buildup"][6]["board"],|p|&p["board"]);
    assert_eq!(end,&scenario["buildup"][6]["goal_board"]);assert_eq!(end["planned_stars"],scenario["planned_stars"]);assert_eq!(end["score"],scenario["score"]);
    assert_eq!(planner::replan_buildup(&data,&scenario,None,None,None,None).unwrap(),scenario);
    assert_eq!(planner::replan_buildup(&data,&scenario,None,Some(&Value::Null),None,None).unwrap(),scenario);
    let text=planner::format_buildup(&scenario,&data).unwrap();
    for expected in ["Early availability assumption: Goal0, from level 3, planned 3 stars","static pre-game what-if","not owned copies"]{assert!(text.contains(expected),"{expected}");}
    let cleared=planner::replan_buildup(&data,&scenario,Some(&json!({})),Some(&json!({})),None,None).unwrap();
    assert_eq!(cleared,baseline);assert_eq!(cleared["planned_stars"]["Goal0"],1);
    for step in cleared["buildup"].as_array().unwrap().iter().take(6){for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){assert!(!names(&phase["board"]).contains("Goal0"));}}
    assert_eq!(baseline,original);assert_eq!(data.raw,original_data);
}

#[test]
fn nonfinal_early_carry_is_temporary_and_future_choices_preserve_scenario(){
    let data=choice_data();
    let baseline=planner::recommend_with_buildups(&data,&options(json!({"size":7,"locked":["Goal0","Goal1","Goal2","Goal3","Goal4","Goal5","Temp00"],"min_collab":0,"limit":1}))).unwrap().remove(0);
    let assumptions=json!({"Goal6":{"level":3,"stars":2}});
    let scenario=planner::replan_buildup(&data,&baseline,Some(&json!({})),Some(&assumptions),None,None).unwrap();
    assert!(names(&scenario["buildup"][2]["board"]).contains("Goal6"));assert!(!names(&scenario).contains("Goal6"));assert_eq!(scenario["champions"],baseline["champions"]);assert_explicit_stars(&scenario);
    let option=scenario["buildup"][3]["temporary_options"].as_array().unwrap().iter().find(|o|o["selected"]!="Goal6"&&o["options"].as_array().unwrap().len()>1).unwrap();
    let slot=option["slot"].as_str().unwrap();let replacement=option["options"].as_array().unwrap().iter().filter_map(Value::as_str).find(|name|*name!=option["selected"].as_str().unwrap()).unwrap();
    let choices=json!({"4":{(slot):replacement}});
    let chosen=planner::replan_buildup(&data,&scenario,Some(&choices),None,None,None).unwrap();
    assert_eq!(chosen["early_availability"],assumptions);assert_eq!(chosen["planning_context"]["early_availability"],assumptions);
    assert!(names(&chosen["buildup"][3]["board"]).contains(replacement));assert_eq!(chosen["champions"],baseline["champions"]);
    assert_eq!(planner::replan_buildup(&data,&chosen,None,None,None,None).unwrap(),chosen);
    assert!(planner::replan_buildup(&data,&chosen,None,Some(&json!({})),None,None).unwrap_err().contains("temporary_choices"));
    let cleared=planner::replan_buildup(&data,&chosen,Some(&json!({})),Some(&json!({})),None,None).unwrap();assert_eq!(cleared,baseline);
}

#[test]
fn scenario_rank_floor_at_final_level_still_precedes_explicit_fresh_copy(){
    let data=choice_data();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":7,"locked":(0..7).map(|i|format!("Goal{i}")).collect::<Vec<_>>(),"min_collab":0,"limit":1,"stars":{"Goal0":1},"early_availability":{"Goal0":{"level":7,"stars":3}}}))).unwrap().remove(0);
    assert_explicit_stars(&result);assert_eq!(result["planned_stars"]["Goal0"],1);
    let phases=result["buildup"][6]["upgrades"].as_array().unwrap();
    let upgrade=phases.iter().position(|p|p["star_upgrades"].as_array().into_iter().flatten().any(|u|u["champion"]=="Goal0"&&u["to"]==3)).unwrap();
    let replacement=phases.iter().position(|p|p["copy_replacements"].as_array().into_iter().flatten().any(|u|u["champion"]=="Goal0"&&u["from"]==3&&u["to"]==1)).unwrap();
    assert!(upgrade<replacement);
}

#[test]
fn early_availability_rejects_bad_records_exclusions_slots_and_exclusivity(){
    let mut raw=choice_data().raw;raw["champions"].as_array_mut().unwrap().extend([
        json!({"name":"Variant A","cost":4,"traits":["Band"],"exclusive_group":"variant"}),
        json!({"name":"Variant B","cost":4,"traits":["Band"],"exclusive_group":"variant"}),
        json!({"name":"Weighted","cost":4,"slots":2,"traits":["Band"]}),
        json!({"name":"Unsupported","cost":6,"traits":["Band"]}),
        json!({"name":"Unquested Seven","cost":7,"traits":["Band"]}),
    ]);
    let data=GameData::from_value(raw).unwrap();
    for assumption in [
        json!([]),json!(null),json!({"Unknown":{"level":3,"stars":1}}),json!({"Temp11":{"level":3,"stars":1}}),
        json!({"Goal0":{"level":true,"stars":1}}),json!({"Goal0":{"level":3.5,"stars":1}}),json!({"Goal0":{"level":3,"stars":false}}),json!({"Goal0":{"level":3,"stars":1.5}}),
        json!({"Goal0":{"level":0,"stars":1}}),json!({"Goal0":{"level":8,"stars":1}}),json!({"Goal0":{"level":3,"stars":0}}),json!({"Goal0":{"level":3,"stars":5}}),
        json!({"Goal0":{"level":3}}),json!({"Goal0":{"level":3,"stars":1,"owned":true}}),json!({"Goal0":3}),
        json!({"Weighted":{"level":1,"stars":1}}),json!({"Unsupported":{"level":3,"stars":1}}),json!({"Unquested Seven":{"level":3,"stars":1}}),
        json!({"Variant A":{"level":3,"stars":1},"Variant B":{"level":3,"stars":1}}),
    ]{
        let payload=json!({"size":7,"min_collab":0,"limit":1,"excluded":["Temp11"],"early_availability":assumption});
        assert!(tft_core::dispatch_with_data(&data,"recommend",&payload).is_err(),"{payload}");
    }
    let conflict=json!({"size":7,"min_collab":0,"limit":1,"locked":["Variant A"],"early_availability":{"Variant B":{"level":3,"stars":1}}});
    assert!(tft_core::dispatch_with_data(&data,"recommend",&conflict).is_err());
    let excluded:Vec<_>=(0..7).map(|i|format!("Goal{i}")).chain(["Weighted","Unsupported","Unquested Seven"].into_iter().map(str::to_owned)).collect();
    let fixed=planner::recommend_with_buildups(&data,&options(json!({"size":7,"excluded":excluded,"mode":"high_value","min_collab":0,"limit":1}))).unwrap().remove(0);
    assert!(names(&fixed).contains("Variant A"));assert_eq!(fixed["planning_context"]["locked"],json!([]));
    assert!(planner::replan_buildup(&data,&fixed,Some(&json!({})),Some(&json!({"Variant B":{"level":3,"stars":1}})),None,None).is_err());
}

#[test]
fn scenarios_never_bypass_published_levels_or_akali_setup_and_check(){
    let data=resources::bundled_data();
    for (name,level) in [("Sona",9),("Vladimir",7)]{
        let payload=json!({"size":10,"min_collab":0,"limit":1,"early_availability":{(name):{"level":level,"stars":2}}});
        assert!(tft_core::dispatch_with_data(data,"recommend",&payload).unwrap_err().contains("published unlock level"));
    }
    let goal=["Leona","Garen","Ahri","Akali (K/DA)","Nidalee","Lillia","Sona","Syndra","Neeko","Varus"];
    let assumptions=json!({"Akali (K/DA)":{"level":3,"stars":2}});
    let result=planner::recommend_with_buildups(data,&options(json!({"size":10,"collab":["Varus","Ahri","Kennen","Graves","Gwen"],"locked":goal,"min_collab":0,"mode":"balanced","limit":1,"early_availability":assumptions}))).unwrap().remove(0);
    let phases=actual_phases(&result);let first=phases.iter().position(|p|names(&p["board"]).contains("Akali (K/DA)")).unwrap();
    let stars=|board:&Value|names(board).iter().filter(|n|n.as_str()!="Akali (K/DA)"&&data.champions[*n].traits.iter().any(|t|t=="K/DA")).map(|n|board["planned_stars"][n].as_u64().unwrap()).sum::<u64>();
    assert!(phases[..first].iter().any(|p|p["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Akali (K/DA)")&&stars(&p["board"])>=12));
    for step in result["buildup"].as_array().unwrap().iter().take(3){assert!(!names(&step["board"]).contains("Akali (K/DA)"));}
    assert_explicit_stars(&result);assert_eq!(names(&result),goal.iter().map(|n|(*n).to_owned()).collect());
    let clear=planner::replan_buildup(data,&result,Some(&json!({})),Some(&json!({})),None,None).unwrap();
    assert_eq!(clear["champions"],result["champions"]);assert_explicit_stars(&clear);
    let phases=actual_phases(&clear);let first=phases.iter().position(|p|names(&p["board"]).contains("Akali (K/DA)")).unwrap();
    assert!(phases[..first].iter().any(|p|p["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Akali (K/DA)")&&stars(&p["board"])>=12));
}

#[test]
fn early_helper_propagates_through_global_preflight_and_actual_unlock_preparation(){
    let data=GameData::from_value(json!({"traits":[{"name":"Band","breakpoints":[2],"unique":false}],"champions":[
        {"name":"Cheap A","cost":1,"traits":["Band"]},{"name":"Cheap B","cost":1,"traits":["Band"]},
        {"name":"Early Helper","cost":4,"traits":["Band"],"role":"Attack Marksman"},
        {"name":"Reward","cost":2,"traits":["Band"],"unlockable":true,"unlock_condition":"Have 2-star Early Helper holding 1 item.","unlock_level":"Any","unlock_source":"https://example.test/early-helper"}
    ]})).unwrap();
    let payload=json!({"size":3,"locked":["Cheap A","Cheap B","Reward"],"min_collab":0,"limit":1,"early_availability":{"Early Helper":{"level":2,"stars":2}}});
    let result=tft_core::dispatch_with_data(&data,"recommend",&payload).unwrap().as_array().unwrap()[0].clone();
    let setup=prep(&result["buildup"][1],"Reward");
    assert!(names(&setup["board"]).contains("Early Helper"));assert_eq!(setup["board"]["planned_stars"]["Early Helper"],2);
    let phases=actual_phases(&result);let first=phases.iter().position(|p|names(&p["board"]).contains("Reward")).unwrap();
    assert!(phases[..first].iter().any(|p|p["board"]["planned_stars"]["Early Helper"]==2&&p["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Reward")));
    assert_eq!(names(&result),["Cheap A","Cheap B","Reward"].into_iter().map(str::to_owned).collect());assert_explicit_stars(&result);
}

#[test]
fn sourced_quest_only_assumption_never_becomes_ordinary_shop_access(){
    let mut raw=choice_data().raw;raw["champions"].as_array_mut().unwrap().push(json!({"name":"Quest Seven","cost":7,"traits":["Band"],"unlockable":true,"unlock_condition":"Have Temp00 holding 1 item.","unlock_level":"Any","unlock_source":"https://example.test/quest-seven"}));
    let data=GameData::from_value(raw).unwrap();
    let result=planner::recommend_with_buildups(&data,&options(json!({"size":7,"locked":["Quest Seven","Temp00","Temp01","Temp02","Temp03","Temp04","Temp05"],"min_collab":0,"limit":1,"early_availability":{"Quest Seven":{"level":3,"stars":1}}}))).unwrap().remove(0);
    let phases=actual_phases(&result);let first=phases.iter().position(|p|names(&p["board"]).contains("Quest Seven")).unwrap();
    assert!(phases[..first].iter().any(|p|p["unlock_checks"].as_array().into_iter().flatten().any(|c|c["name"]=="Quest Seven")));
    assert!(std::iter::once(&result["buildup"][2]).chain(result["buildup"][2]["upgrades"].as_array().unwrap()).any(|p|names(&p["board"]).contains("Quest Seven")));
    for step in result["buildup"].as_array().unwrap(){assert!(!names(&step["fallback"]).contains("Quest Seven"));}
    assert!(planner::format_buildup(&result,&data).unwrap().contains("quest-only, not a regular shop roll"));
}

#[test]
fn multiple_early_assumptions_prefer_compatible_units_without_inventing_slots(){
    let data=choice_data();let baseline=choice_result(&data);
    let assumptions=json!({"Goal0":{"level":1,"stars":2},"Goal1":{"level":1,"stars":3},"Goal2":{"level":1,"stars":1}});
    let result=planner::replan_buildup(&data,&baseline,Some(&json!({})),Some(&assumptions),None,None).unwrap();
    assert_eq!(board_slots(&data,&result["buildup"][0]["board"]),1);
    let assumed:BTreeSet<_>=["Goal0","Goal1","Goal2"].into_iter().map(str::to_owned).collect();
    assert_eq!(names(&result["buildup"][0]["board"]).intersection(&assumed).count(),1);
    assert!(assumed.is_subset(&names(&result["buildup"][2]["board"])));
    for step in result["buildup"].as_array().unwrap(){for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){if !phase["board"].is_null(){assert_eq!(board_slots(&data,&phase["board"]),step["level"].as_u64().unwrap() as usize);}}}
    assert_eq!(result["champions"],baseline["champions"]);assert_explicit_stars(&result);
}

fn adaptive_data()->GameData{
    let mut raw=choice_data().raw;
    raw["champions"].as_array_mut().unwrap().extend((0..10).map(|i|
        json!({"name":format!("Branch {i:02}"),"cost":3,"traits":["Band"],"role":if i%2==0{"Attack Marksman"}else{"Attack Tank"}})));
    GameData::from_value(raw).unwrap()
}
fn adaptive_source(data:&GameData)->Value{
    let mut result=choice_result(data);
    // A controlled search recommendation: its roster is not a global lock.
    result["planning_context"]["locked"]=json!(["Goal0"]);
    result
}
fn adaptive_choice(result:&Value,level:usize,phase:usize)->(String,String){
    let step=&result["buildup"][level-1];
    let options=if phase==0{&step["temporary_options"]}else{&step["upgrades"][phase-1]["temporary_options"]};
    options.as_array().unwrap().iter().find_map(|option|{
        option["options"].as_array().unwrap().iter().filter_map(Value::as_str)
            .find(|name|name.starts_with("Branch ")&&Some(*name)!=option["selected"].as_str())
            .map(|name|(option["slot"].as_str().unwrap().to_owned(),name.to_owned()))
    }).expect("Controlled fixture must offer a meaningful three-cost branch")
}
fn branch(data:&GameData,result:&Value,level:usize,phase:usize,slot:&str,champion:Value,choices:Value)->Result<Value,String>{
    tft_core::dispatch_with_data(data,"choose_branch",&json!({"result":result,"level":level,"phase":phase,"slot":slot,"champion":champion,"temporary_choices":choices}))
}
#[test]
fn manual_branch_adapts_final_retains_fielded_core_and_reaches_exact_new_goal(){
    let data=adaptive_data();let baseline=adaptive_source(&data);
    let source=planner::replan_buildup(&data,&baseline,Some(&json!({})),Some(&json!({"Goal1":{"level":3,"stars":1}})),None,None).unwrap();
    let original=source.clone();let level=4;
    let (slot,champion)=adaptive_choice(&source,level,0);
    let choices=json!({(level.to_string()):{(slot.clone()):champion.clone()}});
    let result=branch(&data,&source,level,0,&slot,json!(champion),choices.clone()).unwrap();
    assert_ne!(names(&result),names(&source));assert!(names(&result).contains(&champion));
    let core:BTreeSet<_>=names(&source["buildup"][level-1]["board"]).intersection(&names(&source)).cloned().collect();
    assert!(!core.is_empty());assert!(core.is_subset(&names(&result)));assert!(names(&result).contains("Goal0"));
    assert!(core.contains("Goal1"));assert_eq!(source["planning_context"]["locked"],json!(["Goal0"]));
    assert_eq!(result["planning_context"]["locked"],source["planning_context"]["locked"]);
    assert_eq!(result["temporary_choices"],choices);
    assert_eq!(result["branch_selection"]["previous_final"],source["champions"]);
    assert_eq!(result["branch_selection"]["final"],result["champions"]);
    for index in 0..level-1{assert_eq!(result["buildup"][index],source["buildup"][index]);}
    assert_eq!(result["buildup"][level-1]["board"],source["buildup"][level-1]["board"]);
    let selected=result["branch_selection"]["phase"].as_u64().unwrap() as usize;
    assert_eq!(selected,1);
    let swap=&result["buildup"][level-1]["upgrades"][selected-1];
    assert!(names(&swap["board"]).contains(&champion));assert!(swap["reason"].as_str().unwrap().contains("before selling"));
    assert_eq!(swap["board"]["planned_stars"][&champion],1);
    for name in names(&source["buildup"][level-1]["board"]).intersection(&names(&swap["board"])){
        assert_eq!(swap["board"]["planned_stars"][name],source["buildup"][level-1]["board"]["planned_stars"][name]);
    }
    for step in result["buildup"].as_array().unwrap().iter().skip(level-1){assert!(core.is_subset(&names(&step["board"])));}
    for phase in result["buildup"].as_array().unwrap().iter().skip(level-1).flat_map(|step|step["upgrades"].as_array().unwrap()){
        assert!(core.is_subset(&names(&phase["board"])));
        for option in phase["temporary_options"].as_array().unwrap(){assert!(!names(&result).contains(option["selected"].as_str().unwrap()));}
    }
    let last=result["buildup"][6]["upgrades"].as_array().unwrap().last().unwrap();
    assert_eq!(last["board"]["champions"],result["champions"]);assert_eq!(last["board"]["planned_stars"],result["planned_stars"]);
    assert_explicit_stars(&result);assert_eq!(source,original);
    let replayed=planner::replan_buildup(&data,&result,None,None,None,Some(&json!(3))).unwrap();
    assert_eq!(replayed["champions"],result["champions"]);
    assert_eq!(replayed["planned_stars"],result["planned_stars"]);
    assert_eq!(replayed["buildup"][level-2],result["buildup"][level-2]);
    assert_eq!(replayed["buildup"][level-1]["upgrades"][0],swap.clone());
    let cleared=branch(&data,&result,level,selected,&slot,Value::Null,json!({})).unwrap();
    assert_eq!(cleared["champions"],result["champions"]);assert_eq!(cleared["temporary_choices"],json!({}));
}
#[test]
fn branch_from_earned_phase_preserves_history_and_rejects_forged_core_swaps(){
    let data=adaptive_data();let source=adaptive_source(&data);
    let phase=source["buildup"][3]["upgrades"].as_array().unwrap().len();assert!(phase>0);
    let (slot,champion)=adaptive_choice(&source,4,phase);
    let result=branch(&data,&source,4,phase,&slot,json!(champion),json!({"4":{(slot.clone()):champion}})).unwrap();
    assert!(names(&result).contains(&champion));
    for n in 0..3{assert_eq!(result["buildup"][n],source["buildup"][n]);}
    for n in 0..phase{assert_eq!(result["buildup"][3]["upgrades"][n],source["buildup"][3]["upgrades"][n]);}
    assert_eq!(result["branch_selection"]["phase"],phase+1);
    assert_explicit_stars(&result);
    let core=names(&source["buildup"][6]["board"]).intersection(&names(&source)).next().unwrap().clone();
    let error=branch(&data,&source,7,0,&core,json!("Temp10"),json!({"7":{(core.clone()):"Temp10"}})).unwrap_err();
    assert!(error.contains("selected Final roster are protected"),"{error}");
    for payload in [
        json!({"result":source,"level":4,"phase":phase,"slot":slot,"champion":"Missing","temporary_choices":{}}),
        json!({"result":source,"level":4,"phase":999,"slot":slot,"champion":champion,"temporary_choices":{"4":{(slot.clone()):champion}}}),
        json!({"result":source,"level":4,"phase":phase,"slot":slot,"champion":champion,"temporary_choices":{},"extra":true})
    ]{assert!(tft_core::dispatch_with_data(&data,"choose_branch",&payload).is_err());}
}
#[test]
fn route_metadata_recommends_legal_candidates_independently_of_selection(){
    let mut raw=adaptive_data().raw;
    raw["traits"].as_array_mut().unwrap().push(json!({"name":"Detour","breakpoints":[2],"unique":false}));
    raw["champions"].as_array_mut().unwrap().push(json!({"name":"Detour","cost":1,"traits":["Detour"],"role":"Attack Marksman"}));
    let data=GameData::from_value(raw).unwrap();let source=adaptive_source(&data);let mut saw_nonoverlap=false;
    for step in source["buildup"].as_array().unwrap(){
        for phase in std::iter::once(step).chain(step["upgrades"].as_array().unwrap()){
            for option in phase["temporary_options"].as_array().unwrap(){
                let recommended=option["recommended"].as_str().unwrap();
                assert!(option["options"].as_array().unwrap().iter().any(|name|name==recommended));
                for name in option["options"].as_array().unwrap().iter().filter_map(Value::as_str){
                    if name=="Detour"{
                        assert_eq!(option["routes"][name]["final_traits"],json!([]));
                        assert_eq!(option["routes"][name]["branch_traits"],json!(["Detour"]));saw_nonoverlap=true;
                    }else{
                        assert_eq!(option["routes"][name]["final_traits"],json!(["Band"]));
                        assert_eq!(option["routes"][name]["branch_traits"],json!(["Band"]));
                    }
                    assert!(option["routes"][name]["unlock_targets"].is_array());
                }
            }
        }
    }
    assert!(saw_nonoverlap,"Off-goal choices must explain their native branch direction without claiming current Final overlap");
    let (slot,champion)=adaptive_choice(&source,4,0);
    let result=planner::replan_buildup(&data,&source,Some(&json!({"4":{(slot.clone()):champion}})),None,None,None).unwrap();
    let old=source["buildup"][3]["temporary_options"].as_array().unwrap().iter().find(|option|option["slot"]==slot).unwrap();
    let new=result["buildup"][3]["temporary_options"].as_array().unwrap().iter().find(|option|option["slot"]==slot).unwrap();
    assert_eq!(old["recommended"],new["recommended"]);
}

#[test]
fn adaptive_branch_preserves_damage_restrictions_and_real_availability_constraints(){
    let data=adaptive_data();let source=adaptive_source(&data);
    let mut restricted=tft_core::dispatch_with_data(&data,"damage_alternative",&json!({
        "result":source,"level":null,"phase":0,"no_ad_items":true,"no_ap_items":true
    })).unwrap();
    // A Final damage view is the context for adaptation, not a new global search.
    restricted["planning_context"]["current_level"]=json!(4);
    let (slot,champion)=adaptive_choice(&restricted,4,0);
    let choices=json!({"4":{(slot.clone()):champion.clone()}});
    let result=branch(&data,&restricted,4,0,&slot,json!(champion),choices.clone()).unwrap();
    assert_eq!(result["planning_context"]["no_ad_items"],true);
    assert_eq!(result["planning_context"]["no_ap_items"],true);
    assert_eq!(result["planning_context"]["current_level"],4);
    assert_eq!(result["planning_context"]["excluded"],restricted["planning_context"]["excluded"]);
    assert!(result["planned_item_builds"].as_array().unwrap().iter().all(|build|build["kind"]=="tank"));
    assert!(result.get("damage_alternative").is_none());
    let mut excluded=restricted.clone();
    excluded["planning_context"]["excluded"].as_array_mut().unwrap().push(json!(champion));
    assert!(branch(&data,&excluded,4,0,&slot,json!(champion),choices).is_err());
    let (early_slot,_)=adaptive_choice(&restricted,4,0);
    assert!(branch(&data,&restricted,3,0,&early_slot,json!(champion),json!({"3":{(early_slot.clone()):champion}})).is_err());
}

#[test]
fn ordinary_cheap_branch_does_not_add_a_reroll_commitment(){
    let data=adaptive_data();let mut source=adaptive_source(&data);
    source["planning_context"]["path_reroll"]=json!(false);
    let option=source["buildup"][0]["temporary_options"].as_array().unwrap().iter().find(|option|
        option["options"].as_array().unwrap().iter().any(|name|name=="Temp10")&&option["selected"]!="Temp10").unwrap();
    let slot=option["slot"].as_str().unwrap();
    let result=branch(&data,&source,1,0,slot,json!("Temp10"),json!({"1":{(slot):"Temp10"}})).unwrap();
    assert_eq!(result["planning_context"]["stars"].get("Temp10"),None);
    assert_eq!(result["planning_context"]["locked"],source["planning_context"]["locked"]);
    assert_eq!(result["planning_context"]["path_reroll"],false);
    for phase in actual_phases(&result){
        if let Some(rank)=phase["board"]["planned_stars"]["Temp10"].as_u64(){assert!(rank<=2);}
    }
}

#[test]
fn final_damage_change_resumes_the_selected_branch_without_rewriting_its_past(){
    let data=adaptive_data();let source=adaptive_source(&data);
    let (slot,champion)=adaptive_choice(&source,4,0);
    let choices=json!({"4":{(slot.clone()):champion.clone()}});
    let adapted=branch(&data,&source,4,0,&slot,json!(champion),choices.clone()).unwrap();
    let changed=tft_core::dispatch_with_data(&data,"damage_alternative",&json!({
        "result":adapted,"level":null,"phase":0,"no_ad_items":true,"no_ap_items":true
    })).unwrap();
    assert_eq!(changed["champions"],adapted["champions"]);
    assert_eq!(changed["temporary_choices"],choices);
    for level in 0..3{assert_eq!(changed["buildup"][level],adapted["buildup"][level]);}
    let phase=adapted["branch_selection"]["phase"].as_u64().unwrap() as usize;
    assert_eq!(changed["buildup"][3]["board"],adapted["buildup"][3]["board"]);
    for index in 0..phase{assert_eq!(changed["buildup"][3]["upgrades"][index],adapted["buildup"][3]["upgrades"][index]);}
    assert_eq!(changed["branch_selection"]["final"],changed["champions"]);
    let replayed=planner::replan_buildup(&data,&changed,Some(&choices),None,None,None).unwrap();
    assert_eq!(replayed["champions"],changed["champions"]);
    assert_eq!(replayed["temporary_choices"],choices);
}
