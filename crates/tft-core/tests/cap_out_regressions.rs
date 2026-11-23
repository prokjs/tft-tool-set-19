use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use tft_core::{cap_out, guidance, model::{GameData, OptimizeOptions}, optimizer, positioning, resources};

fn roster(board:&Value)->BTreeSet<String> {
    board["champions"].as_array().unwrap().iter().map(|n|n.as_str().unwrap().to_owned()).collect()
}
fn fixture(size:usize)->GameData {
    let mut champions:Vec<_>=(0..size).map(|i|json!({"name":format!("Cheap {i}"),"cost":1,"traits":["Shallow"],
        "role":if i<2 {"Magic Tank"} else {"Attack Marksman"}})).collect();
    champions.push(json!({"name":"Premium Tank","cost":4,"traits":[],"role":"Magic Tank"}));
    champions.push(json!({"name":"Premium Carry","cost":4,"traits":[],"role":"Attack Marksman"}));
    GameData::from_value(json!({"traits":[{"name":"Shallow","breakpoints":[2],"unique":false},{"name":"Collab","breakpoints":[3,5],"unique":false}],"champions":champions})).unwrap()
}
fn source(data:&GameData,names:&[String],size:usize,extra:Value)->Value {
    let mut context=json!({"size":size,"mode":"balanced","min_collab":0,"collab":[],"locked":[],"excluded":[],"stars":{},"sona_band":""});
    context.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
    let options=OptimizeOptions::from_value(&context).unwrap();
    let mut board=optimizer::evaluate_team(data,names,&options.collab,&options.mode,&options.sona_band,&options.stars,Some(size)).unwrap();
    board["level"]=json!(size);
    guidance::annotate_result(&mut board,data).unwrap();
    positioning::annotate_positioning(&mut board,data).unwrap();
    board["planning_context"]=context;
    board["completed_unlocks"]=json!(options.completed_unlocks);
    board["current_level"]=json!(options.current_level);
    board
}
fn cheap_source(data:&GameData,size:usize,extra:Value)->Value {
    source(data,&(0..size).map(|i|format!("Cheap {i}")).collect::<Vec<_>>(),size,extra)
}
fn capped(data:&GameData,result:&Value)->Value {
    cap_out::recommend(data,&json!({"result":result})).unwrap()
}
fn occupied(data:&GameData,board:&Value)->usize {
    roster(board).iter().map(|n|data.champions[n].slots).sum()
}

#[test]
fn ordinary_premium_hits_replace_bronze_fillers_without_forcing_collab() {
    let mut raw=fixture(5).raw;
    raw["champions"].as_array_mut().unwrap().push(json!({"name":"Premium Utility","cost":4,"traits":[],"role":"Magic Caster"}));
    let d=GameData::from_value(raw).unwrap();
    let mut result=cheap_source(&d,5,json!({"collab":["Cheap 0","Cheap 1","Cheap 2"],"min_collab":3}));
    result["planning_context"]["temporary_choices"]=json!({"4":{"Cheap 3":"Cheap 4"}});
    let original=result.clone();
    let out=capped(&d,&result);
    let names=roster(&out["board"]);
    assert!(names.contains("Premium Tank")&&names.contains("Premium Carry"));
    assert!(out["board"]["cost"].as_u64().unwrap()>result["cost"].as_u64().unwrap());
    assert!(out["board"]["frontline"].as_u64().unwrap()>=2);
    assert_eq!(occupied(&d,&out["board"]),5);
    assert_eq!(result,original);
    assert_eq!(out["from_board"]["champions"],result["champions"]);
    assert_eq!(out["item_mode"],"planned");
    assert!(out["from_board"]["planned_item_builds"].as_array().unwrap().iter().all(|build|build["item_ids"].is_array()));
    assert!(roster(&out["board"]).intersection(&BTreeSet::from(["Cheap 0".to_owned(),"Cheap 1".to_owned(),"Cheap 2".to_owned()])).count()<3);
}

#[test]
fn locks_exclusions_and_established_reroll_ranks_remain_authoritative() {
    let mut raw=fixture(5).raw;
    raw["champions"][2]["cost"]=json!(3);
    let d=GameData::from_value(raw).unwrap();
    let result=cheap_source(&d,5,json!({"locked":["Cheap 4"],"excluded":["Premium Carry"],"stars":{"Cheap 2":3}}));
    let out=capped(&d,&result);
    let names=roster(&out["board"]);
    assert!(names.contains("Cheap 4")&&names.contains("Cheap 2"));
    assert!(!names.contains("Premium Carry"));
    assert_eq!(out["board"]["planned_stars"]["Cheap 2"],3);
    for stage in out["phases"].as_array().unwrap() {
        assert!(roster(&stage["board"]).contains("Cheap 4"));
        if roster(&stage["board"]).contains("Cheap 2") {assert_eq!(stage["board"]["planned_stars"]["Cheap 2"],3);}
    }
}

#[test]
fn sourced_shop_access_not_conservative_planner_timing_controls_cap_candidates() {
    let d=fixture(5);
    let at_four=source(&d,&(0..4).map(|i|format!("Cheap {i}")).collect::<Vec<_>>(),4,json!({}));
    let four=capped(&d,&at_four);
    assert!(!roster(&four["board"]).iter().any(|n|n.starts_with("Premium")));
    let five=capped(&d,&cheap_source(&d,5,json!({})));
    assert!(roster(&five["board"]).contains("Premium Carry"));
    assert_eq!(five["level"],5);
}

#[test]
fn already_capped_or_fully_locked_returns_the_real_board_without_fake_phases() {
    let d=fixture(5);
    let locks:Vec<_>=(0..5).map(|i|format!("Cheap {i}")).collect();
    let result=cheap_source(&d,5,json!({"locked":locks}));
    let out=capped(&d,&result);
    assert_eq!(out["board"],out["from_board"]);
    for key in ["phases","added","removed","replacements","item_transfers"] {assert!(out[key].as_array().unwrap().is_empty());}
    assert!(!out["notes"].as_array().unwrap().is_empty());
}

#[test]
fn sequential_exchanges_preserve_exact_slots_and_real_transfer_sources() {
    let d=fixture(5);
    let result=cheap_source(&d,5,json!({}));
    let out=capped(&d,&result);
    let mut previous=out["from_board"].clone();
    let mut exchanges=0;
    for stage in out["phases"].as_array().unwrap() {
        let before=roster(&previous);
        let after=roster(&stage["board"]);
        assert_eq!(occupied(&d,&stage["board"]),5);
        assert_eq!(stage["added"],json!(after.difference(&before).collect::<Vec<_>>()));
        assert_eq!(stage["removed"],json!(before.difference(&after).collect::<Vec<_>>()));
        if stage["kind"]=="exchange" {
            exchanges+=1;
            assert_eq!(stage["added"].as_array().unwrap().len(),1);
            assert_eq!(stage["removed"].as_array().unwrap().len(),1);
            assert_eq!(stage["actions"][0]["action"],"buy_before_sell");
            assert_eq!(stage["actions"][1]["action"],"sell_outgoing");
            assert_eq!(stage["purchases"][0]["copies"],1);
        }
        for transfer in stage["item_transfers"].as_array().unwrap() {
            let outgoing=transfer["outgoing"].as_str().unwrap();
            let incoming=transfer["incoming"].as_str().unwrap();
            assert!(before.contains(outgoing)&&after.contains(incoming));
            assert!(previous["item_holders"].as_object().unwrap().values().any(|n|n.as_str()==Some(outgoing)));
            assert!(stage["board"]["item_holders"].as_object().unwrap().values().any(|n|n.as_str()==Some(incoming)));
            assert_eq!(transfer["method"],if after.contains(outgoing) {"remover_or_later_sale"} else {"sell_outgoing"});
            assert!(transfer["condition"].as_str().unwrap().contains("actually holds"));
        }
        for key in ["traits","planned_stars","lineup_order","positioning","item_holders","planned_item_builds"] {assert!(!stage["board"][key].is_null());}
        previous=stage["board"].clone();
    }
    assert_eq!(exchanges,2);
    assert_eq!(previous["champions"],out["board"]["champions"]);
    assert_eq!(previous["planned_stars"],out["board"]["planned_stars"]);
}

#[test]
fn fresh_premium_tanks_do_not_arrive_with_free_two_star_copies() {
    let d=fixture(10);
    let result=cheap_source(&d,10,json!({}));
    let out=capped(&d,&result);
    let phases=out["phases"].as_array().unwrap();
    let first=phases.iter().position(|p|roster(&p["board"]).contains("Premium Tank")).unwrap();
    assert_eq!(phases[first]["board"]["planned_stars"]["Premium Tank"],1);
    let upgrade=phases.iter().skip(first+1).find(|p|p["star_upgrades"].as_array().unwrap().iter().any(|u|u["champion"]=="Premium Tank")).unwrap();
    let detail=upgrade["star_upgrades"].as_array().unwrap().iter().find(|u|u["champion"]=="Premium Tank").unwrap();
    assert_eq!(detail["from"],1);
    assert_eq!(detail["to"],2);
    assert_eq!(detail["additional_copies"],2);
    assert_eq!(out["board"]["planned_stars"]["Premium Tank"],2);
}

fn sona_source(extra:Value)->Value {
    let d=resources::bundled_data();
    source(d,&["Sona","Elise","Gnar","Katarina","Kayn","Kennen","Lillia","Neeko","Olaf","Sett"].map(str::to_owned),10,extra)
}

#[test]
fn unconfirmed_source_quests_remain_conditional_without_unlocking_new_quests() {
    let d=resources::bundled_data();
    let result=sona_source(json!({"current_level":10}));
    let out=capped(d,&result);
    assert!(roster(&out["from_board"]).contains("Sona"));
    assert_eq!(out["comparison_mode"],"planned_final");
    assert_eq!(out["completed_unlocks"],json!({}));
    assert_eq!(out["selection_comparison"]["source_eligible"],true);
    let assumptions=out["source_unlock_assumptions"].as_array().unwrap();
    assert_eq!(assumptions.len(),1);
    assert_eq!(assumptions[0]["name"],"Sona");
    assert_eq!(assumptions[0]["status"],"conditional_source_copy");
    assert_eq!(assumptions[0]["confirms_unlock"],false);
    assert_eq!(assumptions[0]["confirms_ownership"],false);
    for board in std::iter::once(&out["board"]).chain(out["phases"].as_array().unwrap().iter().map(|p|&p["board"])) {
        assert!(roster(board).iter().all(|n|!d.champions[n].unlockable || n=="Sona"));
        assert_eq!(occupied(d,board),10);
    }
    let locked=sona_source(json!({"locked":["Sona"]}));
    let locked_out=capped(d,&locked);
    assert!(roster(&locked_out["board"]).contains("Sona"));
    assert_eq!(locked_out["completed_unlocks"],json!({}));
}

#[test]
fn planned_akali_two_star_and_sona_compare_to_locke_with_the_same_source_assumptions() {
    let d=resources::bundled_data();
    let core=["Rell","Garen","Elise","Gnar","Katarina","Kennen","Neeko","Sett"];
    let assert_physical_items=|out:&Value| {
        let expected:BTreeMap<_,_>=out["item_budget"].as_array().unwrap().iter()
            .map(|item|(item["id"].as_str().unwrap(),item["item"].as_str().unwrap())).collect();
        assert_eq!(expected.len(),6);
        for board in std::iter::once(&out["from_board"]).chain(std::iter::once(&out["board"]))
            .chain(out["phases"].as_array().unwrap().iter().map(|phase|&phase["board"])) {
            let mut actual=BTreeMap::new();
            for build in board["planned_item_builds"].as_array().unwrap() {
                let ids=build["item_ids"].as_array().unwrap();
                let items=build["items"].as_array().unwrap();
                assert_eq!(ids.len(),items.len());
                assert!(ids.len()<=3);
                for (id,item) in ids.iter().zip(items) {
                    assert!(actual.insert(id.as_str().unwrap(),item.as_str().unwrap()).is_none());
                }
            }
            for item in board["item_bench"].as_array().unwrap() {
                assert!(actual.insert(item["id"].as_str().unwrap(),item["item"].as_str().unwrap()).is_none());
            }
            assert_eq!(actual,expected);
        }
    };
    let assert_unconfirmed_source=|out:&Value| {
        assert_eq!(out["comparison_mode"],"planned_final");
        assert!(out["starting_board"].is_null());
        assert_eq!(out["completed_unlocks"],json!({}));
        assert_eq!(out["available_stars"],json!({"Locke":2}));
        let assumptions=out["source_unlock_assumptions"].as_array().unwrap();
        assert_eq!(assumptions.len(),2);
        for assumption in assumptions {
            assert_eq!(assumption["status"],"conditional_source_copy");
            for key in ["confirms_unlock","confirms_ownership","confirms_additional_copies","confirms_purchases","confirms_cashout"] {
                assert_eq!(assumption[key],false);
            }
        }
    };
    for akali in ["Akali (K/DA)","Akali (True Damage)"] {
        let names:Vec<String>=core.iter().copied().chain([akali,"Sona"]).map(str::to_owned).collect();
        let excluded:Vec<_>=d.champions.keys().filter(|name|!names.contains(name)&&name.as_str()!="Locke").cloned().collect();
        let stars:BTreeMap<_,_>=names.iter().map(|name|(name.clone(),2)).collect();
        let mut result=source(d,&names,10,json!({"locked":core,"excluded":excluded,"stars":stars}));
        let (kind,damage_item)=if d.champions[akali].role.starts_with("Attack ") {
            ("ad","Infinity Edge")
        } else {("ap","Jeweled Gauntlet")};
        result["planned_item_builds"]=json!([
            {"kind":kind,"target":akali,"holder":akali,"items":["Bloodthirster",damage_item,"Titan's Resolve"]},
            {"kind":"tank","target":"Rell","holder":"Rell","items":["Warmog's Armor","Bramble Vest","Dragon's Claw"]}
        ]);
        let payload=json!({"result":result,"available_stars":{"Locke":2}});
        let original=payload.clone();
        let out=cap_out::recommend(d,&payload).unwrap();
        assert_eq!(payload,original);
        assert_physical_items(&out);
        assert_unconfirmed_source(&out);
        assert!(roster(&out["board"]).contains(akali));
        assert!(roster(&out["board"]).contains("Sona"));
        assert!(!roster(&out["board"]).contains("Locke"));
        assert_eq!(out["completed_unlocks"],json!({}));
        assert_eq!(out["available_stars"],json!({"Locke":2}));
        assert_eq!(out["selection_comparison"]["source_eligible"],true);
        assert_eq!(out["source_unlock_assumptions"].as_array().unwrap().len(),2);
        assert_eq!(out["item_budget"].as_array().unwrap().len(),6);
        assert_eq!(out["from_board"]["planned_stars"][akali],2);
        assert!(out["replacement_details"].as_array().unwrap().is_empty());
        let confirmed=cap_out::recommend(d,&json!({"result":result,"available_stars":{"Locke":2},
            "completed_unlocks":{akali:10,"Sona":10}})).unwrap();
        assert_eq!(roster(&out["board"]),roster(&confirmed["board"]));
        assert_eq!(out["selection_comparison"]["selected_score"],confirmed["selection_comparison"]["selected_score"]);
        assert!(confirmed["source_unlock_assumptions"].as_array().unwrap().is_empty());
        assert_physical_items(&confirmed);
        assert_eq!(confirmed["item_budget"],out["item_budget"]);
        assert_eq!(confirmed["available_stars"],out["available_stars"]);
        assert_eq!(confirmed["completed_unlocks"],json!({akali:10,"Sona":10}));
        if akali=="Akali (K/DA)" {
            // Unlike TD Akali, K/DA Akali is an Attack Assassin. Selling her
            // releases this off-role magic item to the retained caster Neeko.
            let mut off_role=result.clone();
            off_role["planned_item_builds"][0]["kind"]=json!("ap");
            off_role["planned_item_builds"][0]["items"][1]=json!("Jeweled Gauntlet");
            let pivot=cap_out::recommend(d,&json!({"result":off_role,"available_stars":{"Locke":2}})).unwrap();
            assert_physical_items(&pivot);
            assert_unconfirmed_source(&pivot);
            assert!(!roster(&pivot["board"]).contains(akali));
            assert!(roster(&pivot["board"]).contains("Locke"));
            assert_eq!(pivot["selection_comparison"]["source_eligible"],true);
            assert_eq!(pivot["from_board"]["item_fit"]["score"],50.5);
            assert_eq!(pivot["board"]["item_fit"]["score"],60.0);
            let detail=pivot["replacement_details"].as_array().unwrap().iter().find(|detail|detail["name"]==akali).unwrap();
            assert_eq!(detail["kind"],"heuristic");
            assert_eq!(detail["conditional_source_unlock"],true);
            assert!(pivot["selection_comparison"]["selected_score"].as_f64().unwrap()>pivot["selection_comparison"]["source_score"].as_f64().unwrap());
            let confirmed_pivot=cap_out::recommend(d,&json!({"result":off_role,"available_stars":{"Locke":2},
                "completed_unlocks":{akali:10,"Sona":10}})).unwrap();
            assert_eq!(roster(&pivot["board"]),roster(&confirmed_pivot["board"]));
            assert_eq!(pivot["selection_comparison"]["selected_score"],confirmed_pivot["selection_comparison"]["selected_score"]);
            assert_physical_items(&confirmed_pivot);
            assert_eq!(confirmed_pivot["item_budget"],pivot["item_budget"]);
            assert_eq!(confirmed_pivot["available_stars"],pivot["available_stars"]);
            assert_eq!(confirmed_pivot["completed_unlocks"],json!({akali:10,"Sona":10}));
        }
    }
}

#[test]
fn conditional_source_does_not_override_minimums_or_confirmed_exclusive_variants() {
    let d=resources::bundled_data();
    let low=source(d,&["Sona","Elise","Gnar","Katarina","Kayn","Kennen","Lillia","Neeko"].map(str::to_owned),8,json!({}));
    let low_out=capped(d,&low);
    assert!(!roster(&low_out["board"]).contains("Sona"));
    assert!(low_out["source_unlock_assumptions"].as_array().unwrap().is_empty());
    assert!(low_out["replacement_details"].as_array().unwrap().iter().any(|detail|
        detail["name"]=="Sona"&&detail["kind"]=="eligibility"&&detail["code"]=="published_minimum"));
    let variants=source(d,&["Akali (K/DA)","Elise","Gnar","Katarina","Kayn","Kennen","Lillia","Neeko","Olaf","Sett"].map(str::to_owned),
        10,json!({"completed_unlocks":{"Akali (True Damage)":10}}));
    let out=capped(d,&variants);
    assert!(!roster(&out["board"]).contains("Akali (K/DA)"));
    assert!(out["replacement_details"].as_array().unwrap().iter().any(|detail|
        detail["name"]=="Akali (K/DA)"&&detail["code"]=="exclusive_variant"));
}


#[test]
fn a_conditional_source_quest_can_be_replaced_for_heuristic_value_not_eligibility() {
    let mut raw=fixture(8).raw;
    for name in ["Source Quest","New Quest"] {
        raw["champions"].as_array_mut().unwrap().push(json!({"name":name,"cost":if name=="New Quest" {7} else {4},"traits":[],
            "role":"Attack Marksman","unlockable":true,"unlock_level":"Level 8",
            "unlock_condition":"Complete the sourced task.","unlock_source":"https://example.test/task"}));
    }
    let data=GameData::from_value(raw).unwrap();
    let core:Vec<_>=(0..7).map(|i|format!("Cheap {i}")).collect();
    let names:Vec<_>=core.iter().cloned().chain(["Source Quest".to_owned()]).collect();
    let result=source(&data,&names,8,json!({"locked":core,"stars":{"Source Quest":1}}));
    let baseline=capped(&data,&result);
    assert!(roster(&baseline["board"]).contains("Source Quest"));
    assert!(!roster(&baseline["board"]).contains("New Quest"));
    let out=cap_out::recommend(&data,&json!({"result":result,"available_stars":{"Premium Carry":3}})).unwrap();
    assert_eq!(out["selection_comparison"]["source_eligible"],true);
    assert!(roster(&out["board"]).contains("Premium Carry"));
    assert!(!roster(&out["board"]).contains("Source Quest"));
    assert!(!roster(&out["board"]).contains("New Quest"));
    assert_eq!(out["source_unlock_assumptions"][0]["name"],"Source Quest");
    assert_eq!(out["source_unlock_assumptions"][0]["retained"],false);
    let detail=out["replacement_details"].as_array().unwrap().iter().find(|detail|detail["name"]=="Source Quest").unwrap();
    assert_eq!(detail["kind"],"heuristic");
    assert_eq!(detail["code"],"heuristic_selection");
    assert_eq!(detail["conditional_source_unlock"],true);
    assert!(detail["reason"].as_str().unwrap().contains("unchecked task is not the reason"));
    assert!(out["selection_comparison"]["selected_score"].as_f64().unwrap()>out["selection_comparison"]["source_score"].as_f64().unwrap());
    let mut new_locked=result.clone();
    new_locked["planning_context"]["locked"]=json!(["New Quest"]);
    let error=cap_out::recommend(&data,&json!({"result":new_locked,"starting_board":{"champions":names}})).unwrap_err();
    assert!(error.contains("locked New Quest")&&error.contains("confirmed"));
}
#[test]
fn confirmed_quests_respect_source_minima_exclusions_and_explicit_clear() {
    let d=resources::bundled_data();
    let locked=sona_source(json!({"locked":["Sona"],"completed_unlocks":{"Sona":10},"current_level":10}));
    let out=capped(d,&locked);
    assert!(roster(&out["board"]).contains("Sona"));
    assert_eq!(out["completed_unlocks"]["Sona"],10);
    let cleared=cap_out::recommend(d,&json!({"result":locked,"completed_unlocks":{}})).unwrap();
    assert!(roster(&cleared["board"]).contains("Sona"));
    assert_eq!(cleared["completed_unlocks"],json!({}));
    assert_eq!(cleared["source_unlock_assumptions"][0]["name"],"Sona");
    assert!(cap_out::recommend(d,&json!({"result":locked,"completed_unlocks":{"Sona":9}})).is_err());
    let excluded=sona_source(json!({"excluded":["Sona"],"completed_unlocks":{"Sona":10}}));
    assert!(!roster(&capped(d,&excluded)["board"]).contains("Sona"));
    let unlocked=sona_source(json!({"completed_unlocks":{"Sona":10}}));
    let reset=cap_out::recommend(d,&json!({"result":unlocked,"completed_unlocks":{},"current_level":8})).unwrap();
    assert_eq!(reset["selection_comparison"]["source_eligible"],true);
    assert_eq!(reset["source_unlock_assumptions"][0]["name"],"Sona");
    assert_eq!(reset["completed_unlocks"],json!({}));
    assert_eq!(reset["current_level"],8);
}

#[test]
fn jazz_scores_actual_active_nonunique_traits_without_needing_a_collab_bridge() {
    let d=resources::bundled_data();
    let empty=BTreeMap::new();
    let score=|names:&[&str],data:&GameData|optimizer::evaluate_team(data,&names.iter().map(|s|s.to_string()).collect::<Vec<_>>(),&[],"cap_out","",&empty,Some(10)).unwrap()["score"].as_f64().unwrap();
    let names=["Bard","Galio","Rell","Mel","Garen","Ahri"];
    let mut raw=d.raw.clone();
    for champion in raw["champions"].as_array_mut().unwrap() {
        if champion["name"]=="Bard"||champion["name"]=="Galio" {
            champion["traits"].as_array_mut().unwrap().retain(|t|t!="Jazz");
        }
    }
    let no_jazz=GameData::from_value(raw).unwrap();
    assert!(score(&names,d)>score(&names,&no_jazz));
    let inactive=["Bard","Rell","Mel","Garen","Ahri"];
    assert_eq!(score(&inactive,d),score(&inactive,&no_jazz));
    let no_collab=optimizer::evaluate_team(d,&names.map(str::to_owned),&[],"cap_out","",&empty,Some(10)).unwrap();
    let active=no_collab["active"].as_u64().unwrap();
    assert_eq!(score(&names,d)-score(&names,&no_jazz),30.0*active as f64);
}

#[test]
fn rell_r_and_b_is_team_utility_but_beat_master_is_not_a_team_buff() {
    let d=resources::bundled_data();
    let score=|data:&GameData|optimizer::evaluate_team(data,&["Rell".to_owned()],&[],"cap_out","",&BTreeMap::new(),Some(10)).unwrap()["score"].as_f64().unwrap();
    let mut raw=d.raw.clone();
    let rell=raw["champions"].as_array_mut().unwrap().iter_mut().find(|c|c["name"]=="Rell").unwrap();
    rell["traits"].as_array_mut().unwrap().retain(|t|t!="Beat Master");
    let no_beat=GameData::from_value(raw.clone()).unwrap();
    assert_eq!(score(d),score(&no_beat));
    let rell=raw["champions"].as_array_mut().unwrap().iter_mut().find(|c|c["name"]=="Rell").unwrap();
    rell["traits"].as_array_mut().unwrap().retain(|t|t!="R&B");
    let no_team=GameData::from_value(raw).unwrap();
    assert_eq!(score(d)-score(&no_team),220.0);
}

#[test]
fn weighted_units_exchange_whole_slot_groups_and_confirmed_variants_block_alternatives() {
    let mut raw=fixture(5).raw;
    for (name,group,slots) in [("Weighted",None,2),("Variant A",Some("variant"),1),("Variant B",Some("variant"),1)] {
        raw["champions"].as_array_mut().unwrap().push(json!({"name":name,"cost":4,"slots":slots,"traits":[],"role":"Attack Marksman",
            "unlockable":true,"unlock_level":"Level 5","unlock_condition":"Complete the sourced task.","unlock_source":"https://example.test/unlocks","exclusive_group":group}));
    }
    let d=GameData::from_value(raw).unwrap();
    let stars:BTreeMap<_,_>=(0..5).map(|i|(format!("Cheap {i}"),1)).chain([("Weighted".to_owned(),2)]).collect();
    let result=cheap_source(&d,5,json!({"stars":stars,"completed_unlocks":{"Weighted":5,"Variant A":5}}));
    let out=cap_out::recommend(&d,&json!({"result":result,"available_stars":{"Weighted":2}})).unwrap();
    assert!(roster(&out["board"]).contains("Weighted"));
    assert!(!roster(&out["board"]).contains("Variant B"));
    let mut without_tied_carry=result.clone();
    without_tied_carry["planning_context"]["excluded"]=json!(["Premium Carry"]);
    let available=cap_out::recommend(&d,&json!({"result":without_tied_carry,"available_stars":{"Weighted":2}})).unwrap();
    assert!(roster(&available["board"]).contains("Variant A"));
    let swap=out["phases"].as_array().unwrap().iter().find(|p|p["added"].as_array().unwrap().contains(&json!("Weighted"))).unwrap();
    assert_eq!(swap["removed"].as_array().unwrap().len(),2);
    assert_eq!(swap["added"].as_array().unwrap().len(),1);
    for p in out["phases"].as_array().unwrap() {assert_eq!(occupied(&d,&p["board"]),5);}
    assert!(cap_out::recommend(&d,&json!({"result":result,"completed_unlocks":{"Weighted":5,"Variant A":5,"Variant B":5}})).is_err());
}

#[test]
fn malformed_tracking_or_missing_final_context_is_rejected_without_mutation() {
    let d=fixture(5);
    let result=cheap_source(&d,5,json!({}));
    let original=result.clone();
    for update in [json!({"current_level":1.5}),json!({"current_level":6}),json!({"completed_unlocks":{"Cheap 0":3}}),json!({"completed_unlocks":[]})] {
        let mut payload=json!({"result":result});
        payload.as_object_mut().unwrap().extend(update.as_object().unwrap().clone());
        assert!(cap_out::recommend(&d,&payload).is_err());
    }
    assert!(cap_out::recommend(&d,&json!({"result":{"champions":[]}})).is_err());
    assert_eq!(result,original);
}

#[test]
fn existing_modes_keep_their_scores_while_cap_out_rewards_premium_roles() {
    let d=fixture(5);
    let names=["Premium Carry".to_owned(),"Premium Tank".to_owned()];
    for (mode,expected) in [("balanced",50.0),("high_value",290.0),("synergies",0.0)] {
        let board=optimizer::evaluate_team(&d,&names,&[],mode,"",&BTreeMap::new(),Some(5)).unwrap();
        assert_eq!(board["score"].as_f64().unwrap(),expected);
    }
    let cap=optimizer::evaluate_team(&d,&names,&[],"cap_out","",&BTreeMap::new(),Some(5)).unwrap();
    assert_eq!(cap["score"].as_f64().unwrap(),784.0);
}

#[test]
fn replacing_a_group_of_explicitly_excluded_targets_warns_before_the_overhaul() {
    let mut raw=fixture(8).raw;
    let quests:Vec<String>=(0..3).map(|i|format!("Quest {i}")).collect();
    for name in &quests {
        raw["champions"].as_array_mut().unwrap().push(json!({"name":name,"cost":4,"traits":[],
            "role":"Magic Caster","unlockable":true,"unlock_level":"Any",
            "unlock_condition":"Complete the sourced quest.","unlock_source":"https://example.test/quest"}));
    }
    let data=GameData::from_value(raw).unwrap();
    let names:Vec<String>=quests.iter().cloned().chain((0..5).map(|i|format!("Cheap {i}"))).collect();
    let original=source(&data,&names,8,json!({"excluded":quests}));
    let out=capped(&data,&original);
    let exchange=out["phases"].as_array().unwrap().iter().find(|phase| {
        let removed:BTreeSet<_>=phase["removed"].as_array().unwrap().iter().filter_map(Value::as_str).collect();
        quests.iter().all(|name|removed.contains(name.as_str()))
    }).expect("The unavailable targets must leave together before an eligible board is fielded.");
    assert!(exchange["pivot_warning"].is_object());
    assert_eq!(exchange["pivot_warning"]["level"],8);
    assert_eq!(occupied(&data,&exchange["board"]),8);
}
